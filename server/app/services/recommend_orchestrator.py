"""
/recommend 的编排层 · 全部从 DB 读

流程：
  1. 读 catalog：水果主档、箱型策略、实时库存
  2. selector.solve() 约束求解（代码精算）
  3. write_copy() 让 LLM 写 reason + guide_md
  4. 拼装响应 + 落缓存 + 落 llm_call_logs
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.db import get_pool
from app.schemas import RecommendLLMInput, UserProfile
from app.services import catalog
from app.services.recommend import write_copy
from app.services.selector import SelectionError, SelectionTrace, compute_nutrition, solve, _validate


logger = logging.getLogger(__name__)


# 每周"换一批"上限
REGEN_MAX_PER_WEEK = 2
_SYSTEM_CODE_RE = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b")


class InventoryInsufficient(Exception):
    """对外保留这个类名，向后兼容 api/recommend.py"""


class RegenLimitExceeded(Exception):
    """本周换一批次数已用完"""


GOAL_TAG_MAP = {
    "weight_loss":     "weight_management",
    "glucose_control": "glucose_control",
    "prenatal":        "prenatal",
    "anti_aging":      "antioxidant_focus",
    "immune":          "immune_boost",
    "immune_low":      "immune_boost",
    "gut_health":      "high_fiber",
    "sports_recovery": "recovery",
    "fatigue":      "iron_rich",
    "insomnia":     "magnesium_rich",
    "hair_loss":    "zinc_rich",
    "constipation": "high_fiber",
    "dull_skin":    "antioxidant_focus",
    "high_glucose": "glucose_control",
    "high_pressure": "potassium_rich",
    "high_uric":    "low_fructose",
    "easy_cold":    "immune_boost",
    "period_discomfort": "iron_rich",
}


def _calc_bmi_band(height_cm: float | None, weight_kg: float | None) -> tuple[float | None, str | None]:
    if not height_cm or not weight_kg:
        return None, None
    h_m = height_cm / 100
    if h_m <= 0:
        return None, None
    bmi = round(weight_kg / (h_m * h_m), 2)
    if bmi < 18.5:
        return bmi, "underweight"
    if bmi < 24:
        return bmi, "normal"
    if bmi < 28:
        return bmi, "overweight"
    return bmi, "obese"


def _tags_from_goals(goals: list[str]) -> list[str]:
    tags: list[str] = []
    for g in goals:
        tag = GOAL_TAG_MAP.get(g)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


# ---------- 画像/订阅读取 ----------

async def _load_profile(user_id: UUID) -> tuple[UserProfile, str | None, str | None, dict]:
    """
    返回 (UserProfile, bmi_band, prenatal_stage, lifestyle_dict)
    lifestyle_dict 用于传给 LLM 写个性化文案，不参与 selector 打分。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT gender, height_cm, weight_kg, bmi, bmi_band,
                   goals, allergies, dislikes, prenatal_stage,
                   veg_freq, sleep_pattern, exercise_freq, taste_prefer
              FROM health_profiles
             WHERE user_id = $1 AND is_current = TRUE
             LIMIT 1
            """,
            user_id,
        )
    if row is None:
        raise PermissionError("profile_required")

    goals = list(row["goals"] or [])
    # 把"主目标 + 症状"映射到 selector 可理解的 tag 词汇
    tags: list[str] = []
    tags = _tags_from_goals(goals)

    # 读最近 7 天 rating<=2 的反馈水果，作为"临时降权" hint
    recent_negative = await _load_recent_negatives(user_id)

    profile = UserProfile(
        user_id=user_id,
        goals=goals,
        tags=tags,
        allergies=list(row["allergies"] or []),
        dislikes=list(row["dislikes"] or []),
        recent_negative_feedback=recent_negative,
    )
    lifestyle = {
        "gender": row["gender"],
        "height_cm": float(row["height_cm"]) if row["height_cm"] is not None else None,
        "weight_kg": float(row["weight_kg"]) if row["weight_kg"] is not None else None,
        "bmi": float(row["bmi"]) if row["bmi"] is not None else None,
        "bmi_band": row["bmi_band"],
        "prenatal_stage": row["prenatal_stage"],
        "veg_freq": row["veg_freq"],
        "sleep_pattern": row["sleep_pattern"],
        "exercise_freq": row["exercise_freq"],
        "taste_prefer": list(row["taste_prefer"] or []),
    }
    return profile, row["bmi_band"], row["prenatal_stage"], lifestyle


async def _load_recent_negatives(user_id: UUID) -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT fruit_id::text AS fruit_code
              FROM feedbacks
             WHERE user_id = $1
               AND rating IS NOT NULL AND rating <= 2
               AND created_at > now() - interval '14 days'
            """,
            user_id,
        )
        signal_rows = await conn.fetch(
            """
            SELECT signal_key AS fruit_code
              FROM user_preference_signals
             WHERE user_id = $1
               AND signal_type = 'fruit'
               AND weight <= -3
            """,
            user_id,
        )
    out: list[str] = []
    for r in list(rows) + list(signal_rows):
        code = r["fruit_code"]
        if code and code not in out:
            out.append(code)
    return out


async def _load_feedback_learning(
    user_id: UUID,
    selected_codes: set[str],
    *,
    before: datetime | None = None,
) -> dict:
    """
    把最近反馈转成用户能看懂的学习说明。

    这里只做展示层总结；真正的负反馈降权仍由 _load_recent_negatives + selector 完成。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.fruit_id, fr.name_cn, fr.emoji,
                   f.kind::text AS kind, f.rating, f.comment, f.created_at
             FROM feedbacks f
              LEFT JOIN fruits fr ON fr.code = f.fruit_id
             WHERE f.user_id = $1
               AND f.created_at > now() - interval '30 days'
               AND ($2::timestamptz IS NULL OR f.created_at <= $2)
             ORDER BY f.created_at DESC
             LIMIT 12
            """,
            user_id,
            before,
        )
        signal_rows = await conn.fetch(
            """
            SELECT s.signal_type, s.signal_key, s.weight, s.source_count,
                   fr.name_cn, fr.emoji
              FROM user_preference_signals s
              LEFT JOIN fruits fr
                ON s.signal_type = 'fruit' AND fr.code = s.signal_key
             WHERE s.user_id = $1
               AND ($2::timestamptz IS NULL OR s.last_seen_at <= $2)
             ORDER BY abs(s.weight) DESC, s.last_seen_at DESC
             LIMIT 8
            """,
            user_id,
            before,
        )

    items: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _comment_message(name: str, comment: str) -> tuple[str, str] | None:
        if not comment:
            return None
        rules = [
            ("avoid", ("下周别放", "别放", "不想吃"), f"你说下周别放{name}，这周已降低它的权重"),
            ("less_sweet", ("太甜", "降低甜度"), "你觉得上次偏甜，这周会减少高甜口水果"),
            ("less_sour", ("太酸", "减少酸口"), "你觉得上次偏酸，这周会减少酸口水果"),
            ("freshness", ("不新鲜", "新鲜度", "熟度", "太生", "太硬"), "你提到新鲜度/熟度问题，这周会更注意批次"),
            ("less_amount", ("分量偏多", "分量多", "少放一点"), f"你觉得{name}分量偏多，这周会控制它的克重"),
            ("more_amount", ("分量偏少", "分量少", "多放一点"), f"你觉得{name}分量偏少，后续会提高相近口味权重"),
            ("liked", ("喜欢", "保留", "相近口味"), f"你喜欢{name}，这周会保留相近口味"),
        ]
        for action, needles, message in rules:
            if any(n in comment for n in needles):
                return action, message
        return None

    for r in rows:
        code = r["fruit_id"]
        if not code:
            continue
        name = r["name_cn"] or code
        emoji = r["emoji"] or "🍎"
        rating = r["rating"]
        kind = r["kind"] or "taste"
        comment = (r["comment"] or "").strip()

        comment_signal = _comment_message(name, comment)
        if comment_signal:
            action, base_message = comment_signal
            if action == "avoid":
                message = (
                    f"你说下周别放{name}，这周已避开"
                    if code not in selected_codes
                    else f"你说下周别放{name}，这周已降低它的权重"
                )
            else:
                message = base_message
        elif rating is not None and rating <= 2:
            action = "avoided" if code not in selected_codes else "downweighted"
            message = (
                f"你最近给{name}打了低分，这周已避开"
                if action == "avoided"
                else f"你最近给{name}打了低分，这周已降低它的权重"
            )
        elif rating is not None and rating >= 4 and code in selected_codes:
            action = "kept"
            message = f"你之前喜欢{name}，这周保留了相近口味"
        elif comment:
            action = "learned"
            label = {
                "taste": "口味",
                "freshness": "新鲜度",
                "damage": "包装/破损",
                "preference": "偏好",
                "allergy": "过敏",
                "other": "反馈",
            }.get(kind, "反馈")
            message = f"已记录你关于{name}的{label}反馈，下周继续调整"
        else:
            continue

        key = (code, action)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "fruit_code": code,
            "fruit_name_cn": name,
            "emoji": emoji,
            "action": action,
            "message": message,
        })
        if len(items) >= 3:
            break

    for r in signal_rows:
        if len(items) >= 3:
            break
        signal_type = r["signal_type"]
        key = r["signal_key"]
        weight = float(r["weight"])
        action = "preference_signal"
        if signal_type == "fruit":
            name = r["name_cn"] or key
            emoji = r["emoji"] or "🍎"
            if weight <= -3:
                message = (
                    f"系统已学到你不太喜欢{name}，本周会降低它的权重"
                    if key in selected_codes
                    else f"系统已学到你不太喜欢{name}，本周已避开"
                )
            elif weight >= 2:
                message = f"系统已学到你喜欢{name}，后续会保留相近口味"
            else:
                continue
            code = key
        elif signal_type == "taste":
            emoji = "🍽️"
            code = f"taste:{key}"
            if key == "sweet" and weight < 0:
                name = "甜度"
                message = "系统已学到你不喜欢太甜，后续会降低高甜水果权重"
            elif key == "sour" and weight < 0:
                name = "酸度"
                message = "系统已学到你不喜欢太酸，后续会减少酸口水果"
            else:
                continue
        elif signal_type == "freshness" and key == "sensitive":
            emoji = "📦"
            code = "freshness:sensitive"
            name = "新鲜度"
            message = "系统已学到你在意新鲜度，后续会更注意批次和熟度"
        else:
            continue

        dedupe_key = (code, action)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append({
            "fruit_code": code,
            "fruit_name_cn": name,
            "emoji": emoji,
            "action": action,
            "message": message,
        })

    if not items:
        return {
            "title": "反馈会影响下周",
            "summary": "",
            "items": [],
        }

    summary = items[0]["message"]
    if len(items) > 1:
        summary = f"{summary}，另有 {len(items) - 1} 项偏好已应用"

    return {
        "title": "已根据你的反馈调整",
        "summary": summary,
        "items": items,
    }


def _parse_generated_at(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_user_copy(payload: dict) -> dict:
    """
    防止 LLM 失败或旧缓存把 fruit_code 暴露给用户。
    用户侧只应该看到中文水果名；系统编号留在后台和接口字段里。
    """
    out = dict(payload or {})
    items = [it for it in (out.get("items") or []) if isinstance(it, dict)]
    code_to_name = {
        it.get("fruit_code"): (it.get("fruit_name_cn") or it.get("fruit_code"))
        for it in items
        if it.get("fruit_code")
    }

    def _replace_codes(text: str) -> str:
        value = str(text or "")
        for code, name in code_to_name.items():
            if code and name:
                value = value.replace(str(code), str(name))
        return value

    def _serving_tip(name: str) -> str:
        if "火龙果" in name:
            return "切块冷藏后做下午加餐，清爽、有饱腹感"
        if "牛油果" in name or "鳄梨" in name:
            return "熟软后切半，搭配全麦面包或无糖酸奶更适合控糖"
        if "金果" in name or "猕猴桃" in name or "奇异果" in name:
            return "饭后或下午吃，切开后尽快吃完，维 C 保留更好"
        if "番石榴" in name or "芭乐" in name:
            return "切片带皮吃，纤维更足，适合替代零食"
        if "苹果" in name:
            return "洗净连皮吃更有纤维，耐放，可以留到后半周"
        if "蓝莓" in name or "草莓" in name or "樱桃" in name:
            return "前几天优先吃，冷藏后直接做早餐或下午加餐"
        return "分 2-3 次吃更舒服，冷藏后口感更好"

    for it in items:
        if it.get("reason"):
            it["reason"] = _replace_codes(it["reason"])
    out["items"] = items

    guide_md = _replace_codes(out.get("guide_md") or "")
    guide_is_bad = (
        not guide_md.strip()
        or "平均分配到一周内食用" in guide_md
        or bool(_SYSTEM_CODE_RE.search(guide_md))
    )
    if guide_is_bad:
        lines = ["## 本周食用建议", ""]
        lines.append("**最佳时段**：放在早餐后或下午加餐更合适，尽量避免睡前 2 小时集中吃。")
        lines.append("")
        for it in items:
            name = it.get("fruit_name_cn") or it.get("fruit_code") or "本周水果"
            lines.append(f"- **{name}**：{_serving_tip(name)}。")
        lines.append("")
        lines.append("**小贴士**：熟度高和切开的水果优先吃；更耐放的水果可以留到后半周。")
        guide_md = "\n".join(lines)
    out["guide_md"] = guide_md

    daily_plan = out.get("daily_plan") or []
    daily_bad = (
        len(daily_plan) != 7
        or any(
            "按本周建议" in str(row.get("items", "")) or _SYSTEM_CODE_RE.search(str(row.get("items", "")))
            for row in daily_plan
            if isinstance(row, dict)
        )
    )
    if daily_bad:
        names = [it.get("fruit_name_cn") or it.get("fruit_code") for it in items if it.get("fruit_name_cn") or it.get("fruit_code")]
        names = names or ["本周水果"]
        days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        tips = [
            "早餐后吃，肠胃更舒服",
            "下午加餐，替代甜点",
            "冷藏后口感更清爽",
            "切开后尽快吃完",
            "搭配无糖酸奶更稳",
            "运动后少量补充",
            "睡前两小时尽量不吃",
        ]
        daily_plan = []
        for idx, day in enumerate(days):
            first = names[idx % len(names)]
            second = names[(idx + 2) % len(names)] if len(names) > 2 and idx % 2 == 0 else ""
            daily_plan.append({
                "day": day,
                "items": f"{first} + {second}" if second and second != first else first,
                "tip": tips[idx],
            })
    else:
        daily_plan = [
            {**row, "items": _replace_codes(row.get("items", "")), "tip": _replace_codes(row.get("tip", ""))}
            for row in daily_plan
            if isinstance(row, dict)
        ]
    out["daily_plan"] = daily_plan
    return out


async def _attach_feedback_learning_to_cached(user_id: UUID, cached: dict) -> dict:
    cached = _normalize_user_copy(cached)
    items = cached.get("items") or []
    selected_codes = {
        it.get("fruit_code")
        for it in items
        if isinstance(it, dict) and it.get("fruit_code")
    }
    feedback_learning = await _load_feedback_learning(
        user_id,
        selected_codes,
        before=_parse_generated_at(cached.get("generated_at")),
    )
    out = dict(cached)
    trace = dict(out.get("decision_trace") or {})
    trace["feedback_learning"] = feedback_learning
    out["decision_trace"] = trace
    return out


async def _load_subscription_context(user_id: UUID) -> dict | None:
    """
    能看到配单的条件：选了健康方向即可（unpaid 也给看，用作首箱转化）。
    付款后续由前端 payment_state 控制是否露出"立即订阅"。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, plan_code, status, billing_state, charge_count
              FROM subscriptions
             WHERE user_id = $1 AND status IN ('trial','active','paused','unpaid')
             ORDER BY created_at DESC LIMIT 1
            """,
            user_id,
        )
    return dict(row) if row else None


async def _load_subscription_plan(user_id: UUID) -> str | None:
    ctx = await _load_subscription_context(user_id)
    return ctx["plan_code"] if ctx else None


async def _load_subscription_id(user_id: UUID) -> UUID | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT id FROM subscriptions
             WHERE user_id = $1 AND status IN ('trial','active','paused','unpaid')
             ORDER BY created_at DESC LIMIT 1
            """,
            user_id,
        )


async def _deduct_inventory(items) -> None:
    """
    原子扣减库存：按 batch_id + 剩余量，防止卖超。
    失败时吞掉（MVP 阶段周四统一配箱可接受；上线真付款后 raise 出去触发 INVENTORY_INSUFFICIENT）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        for it in items:
            updated = await conn.execute(
                """
                UPDATE inventory_batches
                   SET qty_avail_g = qty_avail_g - $2, updated_at = now()
                 WHERE batch_no = $1
                   AND qty_avail_g >= $2
                   AND is_active
                """,
                it.batch_id, int(it.qty_g),
            )
            # asyncpg execute 返回 "UPDATE n"
            if not updated.endswith(" 1"):
                logger.warning(
                    "inventory deduct failed: batch=%s qty=%sg (rows=%s)",
                    it.batch_id, it.qty_g, updated,
                )
    # 扣完库存要让 catalog 缓存失效，下一次读的是最新余量
    from app.services import catalog as _catalog
    _catalog.invalidate("inventory")


# ---------- 缓存 ----------

async def _read_cache(user_id: UUID, iso_year: int, iso_week: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT payload, created_at, regen_count FROM weekly_recommendations
             WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
             LIMIT 1
            """,
            user_id, iso_year, iso_week,
        )
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["cached"] = True
    payload["generated_at"] = row["created_at"].isoformat()
    payload["regen_count"] = int(row["regen_count"] or 0)
    payload["regen_left"] = max(0, REGEN_MAX_PER_WEEK - payload["regen_count"])
    return _normalize_user_copy(payload)


def _cache_matches_plan(cached: dict, plan_code: str, plan) -> bool:
    if cached.get("plan_code") != plan_code:
        return False
    try:
        total_cost = float(cached.get("total_cost_cny") or 0)
        budget = float(plan.budget_cny)
        soft = plan.soft_prefs or {}
        max_total_ratio = float(soft.get("max_total_ratio", 1.05))
        total_g = float(
            ((cached.get("nutrition_report") or {}).get("total_g"))
            or sum(int(it.get("qty_g") or 0) for it in (cached.get("items") or []))
        )
        item_count = len(cached.get("items") or [])
        max_total_g = float(plan.box_target_g) * max_total_ratio
        min_items = int(soft.get("min_items", 4))
    except (TypeError, ValueError):
        return False
    return (
        total_cost <= budget + 0.01
        and total_g <= max_total_g + 1
        and item_count >= min_items
    )


def _apply_box_stage(plan, subscription_ctx: dict | None) -> tuple[int, float, dict, str]:
    """
    首箱和续费箱分开配置：
    - 首箱：4-5 种，约 2kg，控制获客成本。
    - 后续：5-7 种，约 3kg，匹配 ¥198/周订阅体验。
    """
    soft = dict(plan.soft_prefs or {})
    charge_count = int((subscription_ctx or {}).get("charge_count") or 0)
    billing_state = (subscription_ctx or {}).get("billing_state")
    status = (subscription_ctx or {}).get("status")
    is_first_box = charge_count <= 0 or billing_state != "paid" or status in ("trial", "unpaid")

    if not is_first_box:
        return int(plan.box_target_g), float(plan.budget_cny), soft, "recurring"

    first_cfg = soft.get("first_box") if isinstance(soft.get("first_box"), dict) else {}
    first_soft = dict(soft)
    first_soft.update({
        "min_items": int(first_cfg.get("min_items", 4)),
        "max_items": int(first_cfg.get("max_items", 5)),
        "single_share_max": float(first_cfg.get("single_share_max", 0.34)),
        "target_fill_ratio": float(first_cfg.get("target_fill_ratio", 0.82)),
        "max_total_ratio": float(first_cfg.get("max_total_ratio", 1.08)),
        "max_premium_items": int(first_cfg.get("max_premium_items", 1)),
        "max_highlight_items": int(first_cfg.get("max_highlight_items", 3)),
    })
    first_soft.pop("first_box", None)
    return (
        int(first_cfg.get("box_target_g", 2000)),
        float(first_cfg.get("budget_cny", min(float(plan.budget_cny), 98))),
        first_soft,
        "first",
    )


async def _write_cache(
    user_id: UUID,
    subscription_id: UUID | None,
    plan_code: str,
    iso_year: int,
    iso_week: int,
    payload: dict,
    meta: dict,
    *,
    increment_regen: bool = False,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO weekly_recommendations
                (user_id, subscription_id, plan_code, iso_year, iso_week,
                 payload, total_cost_cny, model, tokens_in, tokens_out, latency_ms,
                 regen_count)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, 0)
            ON CONFLICT (user_id, iso_year, iso_week)
            DO UPDATE SET
                subscription_id = EXCLUDED.subscription_id,
                plan_code = EXCLUDED.plan_code,
                payload = EXCLUDED.payload,
                total_cost_cny = EXCLUDED.total_cost_cny,
                model = EXCLUDED.model,
                tokens_in = EXCLUDED.tokens_in,
                tokens_out = EXCLUDED.tokens_out,
                latency_ms = EXCLUDED.latency_ms,
                regen_count = weekly_recommendations.regen_count + CASE WHEN $12 THEN 1 ELSE 0 END,
                created_at = now()
            """,
            user_id, subscription_id, plan_code, iso_year, iso_week,
            json.dumps(payload, ensure_ascii=False),
            float(payload["total_cost_cny"]),
            meta["model"], meta["input_tokens"], meta["output_tokens"], meta["latency_ms"],
            increment_regen,
        )


async def _log_llm_call(user_id: UUID, purpose: str, meta: dict, payload: dict) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_call_logs
                    (trace_id, user_id, purpose, model,
                     output_preview, tokens_in, tokens_out,
                     cached_tokens, latency_ms, stop_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                meta["trace_id"], user_id, purpose, meta["model"],
                json.dumps(payload, ensure_ascii=False)[:1024],
                meta["input_tokens"], meta["output_tokens"],
                meta.get("cache_read_input_tokens", 0),
                meta["latency_ms"], str(meta.get("stop_reason", "")),
            )
    except Exception:
        logger.exception("llm_call_logs insert failed")


def _preview_reason(it, plan_code: str) -> str:
    n = it.nutrition_per_100g or {}
    pieces: list[str] = []
    if plan_code == "fatloss_gi":
        if n.get("gi", 99) <= 45:
            pieces.append("低 GI")
        if n.get("fiber_g", 0) >= 2:
            pieces.append(f"纤维 {n.get('fiber_g')}g/100g")
        if n.get("sugar_g", 99) <= 10:
            pieces.append("糖负担较低")
    elif plan_code == "prenatal":
        if n.get("folate_ug", 0) >= 20:
            pieces.append("叶酸友好")
        if n.get("vitC_mg", 0) >= 20:
            pieces.append("维 C 充足")
        if n.get("fiber_g", 0) >= 2:
            pieces.append("纤维补给")
    else:
        if n.get("anthocyanin_mg", 0) >= 50:
            pieces.append("花青素突出")
        if n.get("vitC_mg", 0) >= 40:
            pieces.append("维 C 充足")
        if n.get("orac_umol_te", 0) >= 1500:
            pieces.append("抗氧表现好")
    if not pieces:
        pieces.append("适合本周搭配")
    return "、".join(pieces[:3]) + "，先放入试看果箱。"


def _make_preview_personalizations(plan_code: str, bmi_band: str | None, profile: UserProfile) -> list[str]:
    out: list[str] = []
    if plan_code == "fatloss_gi":
        out.append("按控糖轻盈方向优先筛低 GI 和高纤维水果")
        if bmi_band in ("overweight", "obese"):
            out.append("根据 BMI 自动收紧糖分和总量")
    elif plan_code == "prenatal":
        out.append("按孕产方向优先考虑维 C、叶酸和温和口感")
    else:
        out.append("按抗氧方向优先选择花青素和维 C 水果")
    if "high_fiber" in profile.tags:
        out.append("你有肠道/蔬菜偏少信号，纤维权重提高")
    if "antioxidant_focus" in profile.tags:
        out.append("你有抗氧/熬夜信号，莓果和维 C 权重提高")
    if profile.allergies:
        out.append("过敏名单会硬性规避")
    return out[:3]


async def preview_recommendation(raw_profile: dict) -> dict:
    """
    匿名试看配单：真实库存 + 真实 selector，不登录、不落库、不扣库存、不调用 LLM。
    """
    now = datetime.now(timezone.utc)
    plan_code = raw_profile.get("direction") or "antiox"
    if plan_code not in {"fatloss_gi", "prenatal", "antiox"}:
        plan_code = "antiox"

    direction_goal = {
        "fatloss_gi": "weight_loss",
        "prenatal": "prenatal",
        "antiox": "anti_aging",
    }[plan_code]
    goals = [direction_goal]
    goals.extend([g for g in (raw_profile.get("goals") or []) if g not in goals])
    goals.extend([s for s in (raw_profile.get("symptoms") or []) if s != "none" and s not in goals])
    goals = goals[:8]

    height_cm = raw_profile.get("height_cm")
    weight_kg = raw_profile.get("weight_kg")
    bmi, bmi_band = _calc_bmi_band(height_cm, weight_kg)

    profile = UserProfile(
        user_id=UUID("00000000-0000-0000-0000-000000000000"),
        goals=goals,
        tags=_tags_from_goals(goals),
        allergies=list(raw_profile.get("allergies") or []),
        dislikes=list(raw_profile.get("dislikes") or []),
        recent_negative_feedback=[],
    )

    plans = await catalog.load_plans()
    plan = plans.get(plan_code)
    if plan is None:
        raise InventoryInsufficient("暂时没有可用方向")
    fruits = await catalog.load_fruits()
    inventory = await catalog.load_inventory()
    if not inventory:
        raise InventoryInsufficient("本周暂无可用库存")

    box_g = plan.box_target_g
    budget = plan.budget_cny
    hard_rules = dict(plan.hard_rules)
    prenatal_stage = raw_profile.get("prenatal_stage")

    if plan_code == "prenatal":
        by_stage = hard_rules.get("by_stage", {}) if isinstance(hard_rules, dict) else {}
        default_rules = hard_rules.get("default", {}) if isinstance(hard_rules, dict) else {}
        if prenatal_stage and prenatal_stage in by_stage:
            hard_rules = dict(by_stage[prenatal_stage])
        elif default_rules:
            hard_rules = dict(default_rules)

    if plan_code == "fatloss_gi" and bmi_band:
        if bmi_band == "overweight":
            box_g = int(box_g * 0.9)
            hard_rules["max_sugar_g"] = min(hard_rules.get("max_sugar_g", 10), 8)
            hard_rules["min_fiber_g"] = max(hard_rules.get("min_fiber_g", 2), 2.5)
        elif bmi_band == "obese":
            box_g = int(box_g * 0.8)
            hard_rules["max_sugar_g"] = 8
            hard_rules["min_fiber_g"] = 3.0
            hard_rules["max_gi"] = min(hard_rules.get("max_gi", 45), 40)
        elif bmi_band == "underweight":
            box_g = int(box_g * 1.1)
            hard_rules["max_sugar_g"] = 12

    payload = RecommendLLMInput(
        user_profile=profile,
        plan_code=plan_code,
        box_target_g=box_g,
        budget_cny=budget,
        inventory_snapshot=inventory,
    )
    trace = SelectionTrace(
        plan_code=plan_code,
        total_candidates=0,
        filtered_out=[],
        scored=[],
        repair_attempts=[],
        bonus_items=[],
    )
    try:
        selected = solve(
            payload,
            plan_affinity_by_code={code: f.plan_affinity for code, f in fruits.items()},
            forbidden_by_fruit={code: f.forbidden_for for code, f in fruits.items()},
            hard_rules=hard_rules,
            soft_prefs=plan.soft_prefs,
            trace=trace,
        )
    except SelectionError as e:
        raise InventoryInsufficient(e.message) from e

    nutrition = compute_nutrition(selected)
    try:
        _validate(plan_code, nutrition, selected, hard_rules)
    except SelectionError as e:
        raise InventoryInsufficient(e.message) from e

    items = []
    for it in selected:
        fruit = fruits.get(it.fruit_code)
        items.append({
            "fruit_code": it.fruit_code,
            "fruit_name_cn": fruit.name_cn if fruit else it.fruit_code,
            "emoji": fruit.emoji if fruit else "🍎",
            "batch_id": it.batch_id,
            "qty_g": it.qty_g,
            "tag": _preview_reason(it, plan_code).split("，", 1)[0],
            "reason": _preview_reason(it, plan_code),
        })

    return {
        "preview": True,
        "plan_code": plan_code,
        "plan_name": f"{plan.name} · {plan.tagline}",
        "items": items,
        "nutrition_report": nutrition,
        "hits": nutrition.get("hits", []),
        "total_g": nutrition.get("total_g", sum(it.qty_g for it in selected)),
        "first_payment_cny": 99,
        "recurring_cny": float(plan.price_cny),
        "decision_trace": {
            "total_candidates": trace.total_candidates,
            "personalizations": _make_preview_personalizations(plan_code, bmi_band, profile),
        },
        "generated_at": now.isoformat(),
    }


# ---------- 公开接口 ----------

async def current_week_recommendation(
    user_id: UUID, *, force_refresh: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()

    # 先读缓存
    cached = await _read_cache(user_id, iso_year, iso_week)

    if force_refresh:
        # 换一批：检查额度
        current_regen = (cached or {}).get("regen_count", 0)
        if current_regen >= REGEN_MAX_PER_WEEK:
            raise RegenLimitExceeded(
                f"本周换一批已用完（{REGEN_MAX_PER_WEEK}/{REGEN_MAX_PER_WEEK}），"
                "下周四 AI 会自动再配"
            )

    profile, bmi_band, prenatal_stage, lifestyle = await _load_profile(user_id)
    subscription_ctx = await _load_subscription_context(user_id)
    if subscription_ctx is None:
        raise PermissionError("subscription_required")
    plan_code = subscription_ctx["plan_code"]

    # 1. 读 DB catalog
    plans = await catalog.load_plans()
    plan = plans.get(plan_code)
    if plan is None:
        raise ValueError(f"Unknown plan: {plan_code}")

    box_g, budget, soft_prefs, box_stage = _apply_box_stage(plan, subscription_ctx)

    if not force_refresh and cached is not None and _cache_matches_plan(
        cached,
        plan_code,
        type("PlanView", (), {
            "budget_cny": budget,
            "box_target_g": box_g,
            "soft_prefs": soft_prefs,
        })(),
    ):
        cached.setdefault("box_stage", box_stage)
        return await _attach_feedback_learning_to_cached(user_id, cached)

    fruits = await catalog.load_fruits()
    inventory = await catalog.load_inventory()
    if not inventory:
        raise InventoryInsufficient("本周暂无可用库存，请联系运营")

    plan_affinity_by_code = {code: f.plan_affinity for code, f in fruits.items()}
    forbidden_by_fruit = {code: f.forbidden_for for code, f in fruits.items()}

    # 2. 求解 · 按健康方向 + 用户状态调整硬约束
    hard_rules = dict(plan.hard_rules)

    # 孕产 stage 覆盖（hard_rules.by_stage 里存的是分段规则）
    if plan_code == "prenatal":
        by_stage = hard_rules.get("by_stage", {}) if isinstance(hard_rules, dict) else {}
        default_rules = hard_rules.get("default", {}) if isinstance(hard_rules, dict) else {}
        if prenatal_stage and prenatal_stage in by_stage:
            hard_rules = dict(by_stage[prenatal_stage])
        else:
            hard_rules = dict(default_rules) if default_rules else dict(plan.hard_rules)

    if plan_code == "fatloss_gi" and bmi_band:
        if bmi_band == "overweight":
            box_g = int(box_g * 0.9)           # 胖子总量减 10%
            hard_rules["max_sugar_g"] = min(hard_rules.get("max_sugar_g", 10), 8)
            hard_rules["min_fiber_g"] = max(hard_rules.get("min_fiber_g", 2), 2.5)
        elif bmi_band == "obese":
            box_g = int(box_g * 0.8)
            hard_rules["max_sugar_g"] = 8          # 现实可达（继续收到 7 会经常无解）
            hard_rules["min_fiber_g"] = 3.0
            hard_rules["max_gi"] = min(hard_rules.get("max_gi", 45), 40)
        elif bmi_band == "underweight":
            box_g = int(box_g * 1.1)           # 偏瘦的减脂反而要维持体重，略增量
            hard_rules["max_sugar_g"] = 12

    payload = RecommendLLMInput(
        user_profile=profile,
        plan_code=plan_code,
        box_target_g=box_g,
        budget_cny=budget,
        inventory_snapshot=inventory,
    )
    trace = SelectionTrace(
        plan_code=plan_code, total_candidates=0,
        filtered_out=[], scored=[], repair_attempts=[],
        bonus_items=[],
    )
    try:
        selected = solve(
            payload,
            plan_affinity_by_code=plan_affinity_by_code,
            forbidden_by_fruit=forbidden_by_fruit,
            hard_rules=hard_rules,
            soft_prefs=soft_prefs,
            trace=trace,
        )
    except SelectionError as e:
        logger.warning("SelectionError: %s | trace=%s", e.message, trace)
        raise InventoryInsufficient(e.message) from e

    # compute_nutrition 后需要 validate 填 hits
    nutrition = compute_nutrition(selected)
    try:
        _validate(plan_code, nutrition, selected, hard_rules)
    except SelectionError as e:
        raise InventoryInsufficient(e.message) from e

    total_cost = round(
        sum(it.qty_g * it.unit_cost_cny_per_kg / 1000 for it in selected),
        2,
    )

    display_map = {code: (f.name_cn, f.emoji) for code, f in fruits.items()}
    fruit_display = {
        code: {"name_cn": name_cn, "emoji": emoji}
        for code, (name_cn, emoji) in display_map.items()
    }

    # 3. LLM 写文案（reasons + guide_md + greeting + daily_plan）
    reasons, guide_md, greeting, daily_plan, meta = await write_copy(
        plan_code=plan_code,
        profile=profile.model_dump(mode="json"),
        lifestyle=lifestyle,
        items=selected,
        nutrition=nutrition,
        fruit_display=fruit_display,
    )

    # 4. 组装响应
    enriched_items = []
    for it in selected:
        name_cn, emoji = display_map.get(it.fruit_code, (it.fruit_code, "🍎"))
        enriched_items.append({
            "fruit_code": it.fruit_code,
            "fruit_name_cn": name_cn,
            "emoji": emoji,
            "batch_id": it.batch_id,
            "qty_g": it.qty_g,
            "unit_price_cny_per_kg": float(it.unit_cost_cny_per_kg),
            "reason": reasons.get(it.fruit_code, ""),
        })

    # 决策可见化：把被剔除的候选也转成人话
    def _i18n_reason(raw: str) -> str:
        # 把 selector 里的英文 code 翻译成中文
        if raw.startswith("near_expiry_"):
            # near_expiry_4d (moved to bonus) → 近效期 4 天，作为加料赠送
            import re
            m = re.match(r"near_expiry_(\d+)d", raw)
            days = m.group(1) if m else "?"
            return f"近效期仅 {days} 天 · 随单赠送，不计入预算"
        if raw.startswith("allergy:"):
            return f"你对 {raw.split(':',1)[1]} 过敏"
        if raw == "qty_too_low":
            return "库存不足单份分量"
        if raw == "forbidden_for_plan":
            return "此健康方向禁用该水果"
        if raw == "ops_paused":
            return "本周运营暂停选品"
        return raw

    filtered_display = []
    for f in trace.filtered_out:
        # 近效期是"被移到加料"，不是真的被剔除 —— 不再暴露给用户
        if f["reason"].startswith("near_expiry_"):
            continue
        fruit = fruits.get(f["fruit_code"])
        if fruit:
            filtered_display.append({
                "name_cn": fruit.name_cn,
                "emoji": fruit.emoji,
                "reason": _i18n_reason(f["reason"]),
            })
    # 最多展示 3 条避免冗长
    filtered_display = filtered_display[:3]

    # 个性化决策：基于画像 / BMI / 方向策略生成"为你做的调整"（1-3 条）
    personalizations: list[str] = []
    if plan_code == "fatloss_gi" and bmi_band:
        if bmi_band == "overweight":
            personalizations.append(
                f"看到你 BMI 偏高，糖上限从 10g 收紧到 {hard_rules.get('max_sugar_g', 8)}g"
            )
        elif bmi_band == "obese":
            personalizations.append(
                f"采用最严格的控糖规则：糖 ≤ {hard_rules.get('max_sugar_g', 8)}g，GI ≤ {hard_rules.get('max_gi', 40)}"
            )
        elif bmi_band == "underweight":
            personalizations.append("BMI 偏瘦，已放宽糖分限制并增加了总量")

    if plan_code == "prenatal" and prenatal_stage:
        stage_label = {
            "trying": "备孕期", "first_tri": "孕早期", "second_tri": "孕中期",
            "third_tri": "孕晚期", "postpartum": "哺乳期",
        }.get(prenatal_stage)
        if stage_label:
            personalizations.append(f"按「{stage_label}」调整了叶酸、铁的搭配重点")

    # 画像里的症状标签 → 强化方向
    if "antioxidant_focus" in profile.tags or "dull_skin" in (profile.goals or []):
        personalizations.append("你标了抗氧/熬夜，这周花青素水果优先选")
    if "high_fiber" in profile.tags or "constipation" in (profile.goals or []):
        personalizations.append("你关心肠道，这周纤维权重比平均高")
    if "iron_rich" in profile.tags or "fatigue" in (profile.goals or []):
        personalizations.append("你容易疲劳，多配了高铁 + 花青素组合")

    # 过敏
    allergies = profile.allergies or []
    if allergies:
        names = "、".join(allergies[:3])
        more = "等" if len(allergies) > 3 else ""
        personalizations.append(f"过敏名单里的{names}{more}本周自动跳过")

    # 反馈回避
    if profile.recent_negative_feedback:
        personalizations.append("最近评分低的水果，本周已降权处理")

    personalizations = personalizations[:3]
    feedback_learning = await _load_feedback_learning(
        user_id, {it.fruit_code for it in selected}, before=now,
    )

    # 近效期"加料"：单独一块，前端当惊喜赠品展示
    bonus_display = []
    for b in trace.bonus_items:
        fruit = fruits.get(b["fruit_code"])
        if fruit:
            bonus_display.append({
                "fruit_code": b["fruit_code"],
                "fruit_name_cn": fruit.name_cn,
                "emoji": fruit.emoji,
                "qty_g": b["qty_g"],
                "days_left": b["days_left"],
                "note": f"近效期 {b['days_left']} 天，多送给你，尽早吃掉",
            })

    sub_id = await _load_subscription_id(user_id)

    result = {
        "recommendation_id": str(uuid4()),
        "subscription_id": str(sub_id) if sub_id else None,
        "plan_code": plan_code,
        "plan_name": f"{plan.name} · {plan.tagline}",
        "box_stage": box_stage,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "items": enriched_items,
        "nutrition_report": nutrition,
        "guide_md": guide_md,
        "greeting": greeting,
        "daily_plan": daily_plan,
        "total_cost_cny": total_cost,
        "bonus_items": bonus_display,
        "decision_trace": {
            "total_candidates": trace.total_candidates,
            "personalizations": personalizations,
            "feedback_learning": feedback_learning,
            "filtered": filtered_display,
            # repair_attempts / seed 是内部调优日志，不对外暴露
        },
        "cached": False,
        "generated_at": now.isoformat(),
    }
    result = _normalize_user_copy(result)

    await _write_cache(
        user_id, sub_id, plan_code, iso_year, iso_week, result, meta,
        increment_regen=force_refresh,
    )
    await _log_llm_call(user_id, "recommend", meta, result)

    # 读出最新 regen_count 回塞到响应里
    updated = await _read_cache(user_id, iso_year, iso_week)
    if updated is not None:
        result["regen_count"] = updated.get("regen_count", 0)
        result["regen_left"] = updated.get("regen_left", REGEN_MAX_PER_WEEK)

    result_with_meta = dict(result)
    result_with_meta["cached"] = False   # 强制：刚跑出来的不算 cached
    result_with_meta["_meta"] = {
        "model": meta["model"],
        "tokens_in": meta["input_tokens"],
        "tokens_out": meta["output_tokens"],
        "latency_ms": meta["latency_ms"],
    }
    return result_with_meta
