"""
配单求解器 · 纯 Python 约束满足 + 贪心优化

所有配置（硬约束 + 加分项）从 DB 读，见 app.services.catalog
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.schemas import InventoryItem, RecommendLLMInput


# 近效期阈值：≤ 此天数的批次不塞进正单，后续可以当"加料"或 promo 用
NEAR_EXPIRY_DAYS = 4

ALLERGY_ALIASES = {
    "奇异果": "kiwi",
    "猕猴桃": "kiwi",
    "kiwi": "kiwi",
    "柑橘": "citrus",
    "橙子": "citrus",
    "橙": "citrus",
    "柚子": "citrus",
    "葡萄柚": "citrus",
    "芒果": "mango",
    "mango": "mango",
    "草莓": "strawberry",
    "strawberry": "strawberry",
    "莓果": "berry",
    "浆果": "berry",
    "牛油果": "latex",
    "鳄梨": "latex",
    "乳胶": "latex",
    "latex": "latex",
}


# ---------- 数据结构 ----------

@dataclass
class SelectedItem:
    fruit_code: str
    batch_id: str
    qty_g: int
    unit_cost_cny_per_kg: float
    nutrition_per_100g: dict[str, float]
    value_tier: str = "basic"


@dataclass
class SelectionTrace:
    """选品决策日志（前端'为什么这样选'展开区用）"""
    plan_code: str
    total_candidates: int
    filtered_out: list[dict]              # [{fruit_code, reason}]
    scored: list[dict]                    # [{fruit_code, score, picked}]
    repair_attempts: list[str]
    bonus_items: list[dict]               # 近效期加料 [{fruit_code, qty_g, days_left}]
    seed: int = 0                         # 本次随机扰动种子（可观测）


# ---------- 打分（DB 驱动） ----------

def _score(
    item: InventoryItem,
    plan_code: str,
    profile_dislikes: set[str],
    plan_affinity_by_code: dict[str, dict[str, int]],
    hard_rules: dict,
    negative_feedback: set[str] | None = None,
) -> float:
    """综合打分。越高越优先。"""
    score = 50.0  # base

    # 1. 方向适配度（来自 fruits.plan_affinity 运营可配）
    affinity = plan_affinity_by_code.get(item.fruit_code, {}).get(plan_code, 0)
    score += affinity

    # 2. 近效期加权：≤5 天 +30，6-10 +15
    d = item.best_before_days
    if d <= 5:
        score += 30
    elif d <= 10:
        score += 15

    # 2b. 时令加分：当季 +25，反季 -10
    month = date.today().month
    months = item.season_months or []
    if months:
        if month in months:
            score += 25
        else:
            def _month_dist(m: int) -> int:
                d = abs(month - m)
                return min(d, 12 - d)
            if min(_month_dist(m) for m in months) >= 2:
                score -= 10

    # 2c. 产地：国产 +10（供应稳定），进口不加也不减
    if item.origin_region in ("south_china", "north_china"):
        score += 10

    # 2d. 运营状态：本周主推/降权由后台直接控制
    if item.selection_status == "featured":
        score += 35
    elif item.selection_status == "downranked":
        score -= 35
    score += item.ops_weight

    # 3. 用户 dislikes（不完全禁止，只降权）
    if item.fruit_code in profile_dislikes:
        score -= 40
    # 4. 最近 14 天 rating<=2 的水果，临时重扣
    if negative_feedback and item.fruit_code in negative_feedback:
        score -= 60

    # 4. 硬约束贴合度：按规则字段动态调分
    nut = item.nutrition_per_100g
    if "max_gi" in hard_rules:
        score -= max(0, nut.get("gi", 50) - 30) * 0.5
    if "max_sugar_g" in hard_rules:
        score -= max(0, nut.get("sugar_g", 0) - 5) * 2
    if "min_fiber_g" in hard_rules:
        score += min(nut.get("fiber_g", 0), 4) * 5
    if "min_folate_per_portion_ug" in hard_rules:
        score += min(nut.get("folate_ug", 0), 50) * 0.5
    if "min_vitC_mg" in hard_rules:
        score += min(nut.get("vitC_mg", 0), 60) * 0.2
    if "min_vitC_or_anthocyanin" in hard_rules:
        score += min(nut.get("vitC_mg", 0), 100) * 0.3
        score += min(nut.get("anthocyanin_mg", 0), 200) * 0.2

    return score


# ---------- 主算法 ----------

def solve(
    payload: RecommendLLMInput,
    *,
    plan_affinity_by_code: dict[str, dict[str, int]] | None = None,
    forbidden_by_fruit: dict[str, list[str]] | None = None,
    hard_rules: dict | None = None,
    soft_prefs: dict | None = None,
    trace: SelectionTrace | None = None,
    seed: int | None = None,
) -> list[SelectedItem]:
    """
    返回选中的 items 列表。

    新策略（批次 1）：
      1. 先满足硬约束，再在预算内尽量多样化 + 用足预算
      2. 相同分数的水果用 seed 做随机扰动（同一周稳定，不同周变）
      3. 近效期 ≤ 4 天的批次不进正单，在 trace.bonus_items 里记录，由上层决定"加料"
      4. 首箱至少 4 种，后续可配置到 5-7 种，单品 ≤ 34-35%（更有订阅箱感）
      5. 用足预算：选完后如果 cost < budget*0.85，把最受欢迎的 1-2 种加量
    """
    profile = payload.user_profile
    plan = payload.plan_code
    target_g = payload.box_target_g
    budget = payload.budget_cny
    plan_affinity_by_code = plan_affinity_by_code or {}
    forbidden_by_fruit = forbidden_by_fruit or {}
    hard_rules = hard_rules or {}
    soft_prefs = soft_prefs or {}

    max_items = int(soft_prefs.get("max_items", 5))
    min_items = int(soft_prefs.get("min_items", 4))
    single_share_max = float(soft_prefs.get("single_share_max", 0.34))  # 单品 ≤ 34%
    target_fill_ratio = float(soft_prefs.get("target_fill_ratio", 0.92))  # 至少用到 92% 预算
    max_total_ratio = float(soft_prefs.get("max_total_ratio", 1.05))
    premium_unit_cost = float(soft_prefs.get("premium_unit_cost_cny_per_kg", 65))
    max_premium_items = int(soft_prefs.get("max_premium_items", 1))
    max_highlight_items = int(soft_prefs.get("max_highlight_items", 3))

    # 0. 随机种子（保证同一周稳定）
    if seed is None:
        seed = abs(hash(f"{profile.user_id}:{plan}:{target_g}")) % (2**31)
    rng = random.Random(seed)
    if trace is not None:
        trace.seed = seed

    # 1. 过滤
    allergies = _normalize_allergies(profile.allergies)
    dislikes = {d.strip().lower() for d in profile.dislikes}
    negatives = set(profile.recent_negative_feedback or [])

    def keep(item: InventoryItem) -> tuple[bool, str | None]:
        if item.selection_status == "paused":
            return False, "ops_paused"
        if item.qty_avail_g < 150:
            return False, "qty_too_low"
        code_lc = item.fruit_code.lower()
        allergy_tags = {t.strip().lower() for t in item.allergy_tags}
        for a in allergies:
            if a and (a in code_lc or a in allergy_tags):
                return False, f"allergy:{a}"
        if plan in (forbidden_by_fruit.get(item.fruit_code) or []):
            return False, "forbidden_for_plan"
        return True, None

    near_expiry: list[InventoryItem] = []
    candidates: list[InventoryItem] = []
    for it in payload.inventory_snapshot:
        ok, reason = keep(it)
        if not ok:
            if trace is not None:
                trace.filtered_out.append({"fruit_code": it.fruit_code, "reason": reason})
            continue
        if it.best_before_days <= NEAR_EXPIRY_DAYS:
            # 近效期 → 不进正单，进"加料候选"
            near_expiry.append(it)
            if trace is not None:
                trace.filtered_out.append({
                    "fruit_code": it.fruit_code,
                    "reason": f"near_expiry_{it.best_before_days}d (moved to bonus)",
                })
            continue
        candidates.append(it)

    if trace is not None:
        trace.total_candidates = len(payload.inventory_snapshot)
        # 加料额度：近效期里挑 1-2 种，每种 150-250g，由库存决定
        for it in near_expiry[:2]:
            trace.bonus_items.append({
                "fruit_code": it.fruit_code,
                "qty_g": min(250, it.qty_avail_g),
                "days_left": it.best_before_days,
            })

    if not candidates:
        raise SelectionError("INVENTORY_EMPTY", "本周没有适合你的水果库存")

    # 2. 打分 + 扰动 + 排序
    # 扰动范围更大（±20 分），否则分差小的水果还是同个排序
    def _perturbed_score(it: InventoryItem) -> float:
        base = _score(it, plan, dislikes, plan_affinity_by_code, hard_rules, negatives)
        jitter = (rng.random() - 0.5) * 40
        return base + jitter

    scored = sorted(
        [(_perturbed_score(it), it) for it in candidates],
        key=lambda x: -x[0],
    )
    if trace is not None:
        trace.scored = [
            {"fruit_code": it.fruit_code, "score": round(sc, 1), "picked": False}
            for sc, it in scored
        ]

    # 3. 第一轮装箱：多样化 + 保硬约束
    selected = _fill_box(
        scored, target_g, budget, max_items, min_items,
        single_share_max, premium_unit_cost, max_premium_items, max_highlight_items, rng,
    )
    if len(selected) < min_items:
        raise SelectionError("INVENTORY_INSUFFICIENT", "可选品种不足以凑一箱")

    # 4. 硬约束修补
    selected = _repair_if_needed(selected, scored, plan, budget, hard_rules, trace)
    report = compute_nutrition(selected)
    try:
        _validate(plan, report, selected, hard_rules)
    except SelectionError:
        if trace is not None:
            trace.repair_attempts.append("fallback: re-plan with value-first strategy")
        selected = _replan_value_first(
            candidates, plan, hard_rules, soft_prefs, budget, target_g, dislikes, plan_affinity_by_code,
        )
        if len(selected) < min_items:
            raise SelectionError("INVENTORY_INSUFFICIENT", "可选品种不足以凑一箱")
        report = compute_nutrition(selected)
        _validate(plan, report, selected, hard_rules)

    # 5. 预算利用率：如果剩余 > (1 - target_fill_ratio) * budget，加量
    selected = _fill_budget(
        selected, scored, target_g, budget, single_share_max, target_fill_ratio,
        max_total_ratio, trace,
    )

    if trace is not None:
        picked = {s.fruit_code for s in selected}
        for entry in trace.scored:
            entry["picked"] = entry["fruit_code"] in picked

    return selected


def _portion_options(it: InventoryItem, max_for_this: int) -> list[int]:
    """
    基于水果的 serving_size_g 生成可选克重档位：
      - 苹果 200g/个 → [200, 400, 600, 800, 1000]
      - 蓝莓 125g/盒 → [125, 250, 375, 500, 625]
      - 散称 250g/袋 → [250, 500, 750, 1000]
    """
    unit_size = max(it.serving_size_g, 100)
    options = []
    for n in range(1, 10):
        g = unit_size * n
        if g > max_for_this:
            break
        options.append(g)
    return options


def _normalize_allergies(raw: list[str]) -> set[str]:
    out: set[str] = set()
    for item in raw or []:
        key = str(item).strip().lower()
        if not key:
            continue
        out.add(key)
        out.add(ALLERGY_ALIASES.get(key, key))
    return out


def _fill_box(
    scored: list[tuple[float, InventoryItem]],
    target_g: int,
    budget: float,
    max_items: int,
    min_items: int,
    single_share_max: float,
    premium_unit_cost: float,
    max_premium_items: int,
    max_highlight_items: int,
    rng: random.Random,
) -> list[SelectedItem]:
    """
    第一轮装箱：按每种水果真实规格（件/盒/袋）配份数。
    """
    selected: list[SelectedItem] = []
    remaining = target_g
    cost = 0.0

    ideal_portion = target_g // max(min_items + 1, 4)

    def is_premium(item: InventoryItem) -> bool:
        return (
            item.value_tier == "premium"
            or (premium_unit_cost > 0 and item.unit_cost_cny_per_kg >= premium_unit_cost)
        )

    def is_highlight(item: InventoryItem) -> bool:
        return item.value_tier in ("highlight", "premium")

    for _, it in scored:
        if len(selected) >= max_items:
            break
        if remaining <= 100:
            break
        if is_premium(it):
            premium_count = sum(
                1 for s in selected
                if s.value_tier == "premium" or s.unit_cost_cny_per_kg >= premium_unit_cost
            )
            if premium_count >= max_premium_items:
                continue
        if is_highlight(it):
            highlight_count = sum(
                1 for s in selected
                if s.value_tier in ("highlight", "premium") or s.unit_cost_cny_per_kg >= premium_unit_cost
            )
            if highlight_count >= max_highlight_items:
                continue

        max_for_this = min(it.qty_avail_g, int(target_g * single_share_max))
        options = _portion_options(it, max_for_this)
        options = [g for g in options if g <= remaining]
        if not options:
            continue

        # 按 |档位 - ideal| 升序；相同距离时随机打平
        options.sort(key=lambda g: (abs(g - ideal_portion), rng.random()))
        chosen_g = None
        for g in options:
            if cost + g * it.unit_cost_cny_per_kg / 1000 <= budget:
                chosen_g = g
                break
        if chosen_g is None:
            continue

        selected.append(SelectedItem(
            fruit_code=it.fruit_code,
            batch_id=it.batch_id,
            qty_g=chosen_g,
            unit_cost_cny_per_kg=it.unit_cost_cny_per_kg,
            nutrition_per_100g=dict(it.nutrition_per_100g),
            value_tier=it.value_tier,
        ))
        remaining -= chosen_g
        cost += chosen_g * it.unit_cost_cny_per_kg / 1000

    return selected


def _fill_budget(
    selected: list[SelectedItem],
    scored: list[tuple[float, InventoryItem]],
    target_g: int,
    budget: float,
    single_share_max: float,
    target_fill_ratio: float,
    max_total_ratio: float,
    trace: SelectionTrace | None,
) -> list[SelectedItem]:
    """
    选完如果预算没用足（< target_fill_ratio），
    优先给"性价比最高"的已选项加量（不突破 single_share_max）。
    """
    def _cost(items: list[SelectedItem]) -> float:
        return sum(x.qty_g * x.unit_cost_cny_per_kg / 1000 for x in items)

    def _total_g(items: list[SelectedItem]) -> int:
        return sum(x.qty_g for x in items)

    max_total_g = int(target_g * max_total_ratio)
    if _cost(selected) >= budget * target_fill_ratio:
        return selected

    # 按 (单价低 × 用户喜好高)，给排名靠前的加量
    score_by_code = {it.fruit_code: sc for sc, it in scored}
    candidate_boost = sorted(
        range(len(selected)),
        key=lambda i: (
            selected[i].unit_cost_cny_per_kg,     # 便宜的先加
            -score_by_code.get(selected[i].fruit_code, 0),
        ),
    )

    # 通过 cur.fruit_code 找到对应的 scored 入口取出 serving_size_g
    item_by_code = {it.fruit_code: it for _, it in scored}

    for idx in candidate_boost:
        cur = selected[idx]
        step = max(100, (item_by_code.get(cur.fruit_code).serving_size_g if item_by_code.get(cur.fruit_code) else 100))
        cap = int(target_g * single_share_max)
        room_qty = cap - cur.qty_g
        if room_qty < step:
            continue
        room_total = max_total_g - _total_g(selected)
        if room_total < step:
            break
        room_cost = budget - _cost(selected)
        if room_cost < cur.unit_cost_cny_per_kg * step / 1000:
            continue
        max_add_by_budget = int(room_cost / cur.unit_cost_cny_per_kg * 1000)
        add_g = min(room_qty, max_add_by_budget, room_total)
        add_g = (add_g // step) * step   # 按 serving 取整
        if add_g < step:
            continue
        selected[idx] = SelectedItem(
            **{**cur.__dict__, "qty_g": cur.qty_g + add_g},
        )
        if trace is not None:
            trace.repair_attempts.append(
                f"budget-fill: +{add_g}g {cur.fruit_code} (used ¥{_cost(selected):.0f}/¥{budget:.0f})"
            )
        if _cost(selected) >= budget * target_fill_ratio:
            break
    return selected


def _replan_value_first(
    candidates: list[InventoryItem],
    plan: str,
    hard_rules: dict,
    soft_prefs: dict,
    budget: float,
    target_g: int,
    dislikes: set[str],
    plan_affinity_by_code: dict[str, dict[str, int]],
) -> list[SelectedItem]:
    """
    性价比优先的备选策略：
    - 按"关键营养字段 / 单价"打分（花更少钱得更多营养）
    - 仍然保留方向适配度和硬约束方向
    """
    # 针对当前健康方向决定关注哪个营养字段
    key_fields: list[str] = []
    if "min_fiber_g" in hard_rules:
        key_fields.append("fiber_g")
    if "min_vitC_mg" in hard_rules or "min_vitC_or_anthocyanin" in hard_rules:
        key_fields.append("vitC_mg")
    if "min_folate_per_portion_ug" in hard_rules:
        key_fields.append("folate_ug")
    if "max_gi" in hard_rules:
        # GI 越低越值钱（用 1/GI）
        key_fields.append("_inv_gi")
    if not key_fields:
        key_fields = ["vitC_mg"]

    def value(it: InventoryItem) -> float:
        nut = it.nutrition_per_100g
        v = 0.0
        for k in key_fields:
            if k == "_inv_gi":
                gi = nut.get("gi", 50) or 50
                v += max(0, 50 - gi)
            else:
                v += nut.get(k, 0)
        # 性价比：单位成本换多少营养分
        price = max(it.unit_cost_cny_per_kg, 5)
        score = v / price * 10
        score += plan_affinity_by_code.get(it.fruit_code, {}).get(plan, 0) * 0.3
        if it.fruit_code in dislikes:
            score -= 40
        return score

    scored = sorted([(value(it), it) for it in candidates], key=lambda x: -x[0])

    max_items = int(soft_prefs.get("max_items", 5))
    min_items = int(soft_prefs.get("min_items", 4))
    single_share_max = float(soft_prefs.get("single_share_max", 0.34))
    premium_unit_cost = float(soft_prefs.get("premium_unit_cost_cny_per_kg", 65))
    max_premium_items = int(soft_prefs.get("max_premium_items", 1))
    max_highlight_items = int(soft_prefs.get("max_highlight_items", 3))

    selected: list[SelectedItem] = []
    remaining = target_g
    cost = 0.0
    for _, it in scored:
        if remaining <= 100 or len(selected) >= max_items:
            break
        if it.value_tier == "premium" or it.unit_cost_cny_per_kg >= premium_unit_cost:
            premium_count = sum(
                1 for s in selected
                if s.value_tier == "premium" or s.unit_cost_cny_per_kg >= premium_unit_cost
            )
            if premium_count >= max_premium_items:
                continue
        if it.value_tier in ("highlight", "premium") or it.unit_cost_cny_per_kg >= premium_unit_cost:
            highlight_count = sum(
                1 for s in selected
                if s.value_tier in ("highlight", "premium") or s.unit_cost_cny_per_kg >= premium_unit_cost
            )
            if highlight_count >= max_highlight_items:
                continue
        max_for_this = min(it.qty_avail_g, int(target_g * single_share_max))
        # 先试 1000，不行 500
        for g in (1000, 500):
            if g > max_for_this or g > remaining:
                continue
            cost_delta = g * it.unit_cost_cny_per_kg / 1000
            if cost + cost_delta > budget:
                continue
            selected.append(SelectedItem(
                fruit_code=it.fruit_code,
                batch_id=it.batch_id,
                qty_g=g,
                unit_cost_cny_per_kg=it.unit_cost_cny_per_kg,
                nutrition_per_100g=dict(it.nutrition_per_100g),
                value_tier=it.value_tier,
            ))
            remaining -= g
            cost += cost_delta
            break

    if len(selected) < min_items:
        raise SelectionError("INVENTORY_INSUFFICIENT", "可选品种不足以凑一箱")
    return selected


def _repair_if_needed(
    selected: list[SelectedItem],
    scored_candidates: list[tuple[float, InventoryItem]],
    plan: str,
    budget: float,
    hard_rules: dict,
    trace: SelectionTrace | None = None,
) -> list[SelectedItem]:
    """约束不过时，尝试把最弱项换成更合适的（最多 5 次）。"""
    for attempt in range(5):
        report = compute_nutrition(selected)
        try:
            _validate(plan, report, selected, hard_rules)
            return selected
        except SelectionError:
            pass

        avg = report["weighted_avg_per_100g"]
        shortage = _shortage_key(hard_rules, avg)
        if shortage is None:
            return selected

        chosen_codes = {s.fruit_code for s in selected}
        better = None
        for _, it in scored_candidates:
            if it.fruit_code in chosen_codes:
                continue
            if it.nutrition_per_100g.get(shortage, 0) <= avg.get(shortage, 0):
                continue
            better = it
            break
        if better is None:
            if trace is not None:
                trace.repair_attempts.append(f"round{attempt}: no better candidate for {shortage}")
            return selected

        weakest_idx = min(
            range(len(selected)),
            key=lambda i: selected[i].nutrition_per_100g.get(shortage, 0),
        )
        old = selected[weakest_idx]

        # 策略：同克重替换；如果超预算，尝试先把最贵那项缩到 500g 再替换
        def _cost_of(items: list[SelectedItem]) -> float:
            return sum(x.qty_g * x.unit_cost_cny_per_kg for x in items) / 1000

        candidate_selected = [SelectedItem(**s.__dict__) for s in selected]
        # 替换
        candidate_selected[weakest_idx] = SelectedItem(
            fruit_code=better.fruit_code,
            batch_id=better.batch_id,
            qty_g=old.qty_g,
            unit_cost_cny_per_kg=better.unit_cost_cny_per_kg,
            nutrition_per_100g=dict(better.nutrition_per_100g),
            value_tier=better.value_tier,
        )

        if _cost_of(candidate_selected) > budget:
            # 超预算：找除 weakest_idx 外单价最高的，从 1000g 缩到 500g
            other_idx = [i for i in range(len(candidate_selected)) if i != weakest_idx]
            expensive = sorted(other_idx, key=lambda i: -candidate_selected[i].unit_cost_cny_per_kg)
            for idx in expensive:
                if candidate_selected[idx].qty_g >= 1000:
                    candidate_selected[idx] = SelectedItem(
                        **{**candidate_selected[idx].__dict__, "qty_g": 500},
                    )
                    if _cost_of(candidate_selected) <= budget:
                        break
            if _cost_of(candidate_selected) > budget:
                if trace is not None:
                    trace.repair_attempts.append(f"round{attempt}: still over budget after shrink")
                return selected

        selected = candidate_selected
        if trace is not None:
            trace.repair_attempts.append(
                f"round{attempt}: replace {old.fruit_code} -> {better.fruit_code} to boost {shortage}"
            )
    return selected


def _shortage_key(hard_rules: dict, avg: dict) -> str | None:
    """根据规则返回当前最缺的维度（找能替换补足的正向指标）"""
    if "min_fiber_g" in hard_rules and avg.get("fiber_g", 0) < hard_rules["min_fiber_g"]:
        return "fiber_g"
    if "min_vitC_mg" in hard_rules and avg.get("vitC_mg", 0) < hard_rules["min_vitC_mg"]:
        return "vitC_mg"
    if "min_folate_per_portion_ug" in hard_rules and avg.get("folate_ug", 0) < 20:
        return "folate_ug"
    if "min_vitC_or_anthocyanin" in hard_rules:
        if avg.get("vitC_mg", 0) < 30 and avg.get("anthocyanin_mg", 0) < 50:
            return "vitC_mg"
    return None


_NUTRITION_KEYS = (
    "kcal", "sugar_g", "fiber_g", "gi", "vitC_mg",
    "folate_ug", "anthocyanin_mg", "potassium_mg", "magnesium_mg",
    "calcium_mg", "iron_mg", "fructose_g", "orac_umol_te",
)


def compute_nutrition(items: list[SelectedItem]) -> dict[str, Any]:
    """精确计算加权营养报告（代码算，永远对）"""
    total_g = sum(it.qty_g for it in items)
    if total_g == 0:
        return {
            "total_g": 0,
            "weighted_avg_per_100g": {k: 0 for k in _NUTRITION_KEYS},
            "hits": [],
            "gaps": [],
        }

    avg = {}
    for k in _NUTRITION_KEYS:
        s = sum(it.nutrition_per_100g.get(k, 0) * it.qty_g for it in items)
        avg[k] = round(s / total_g, 1)

    return {
        "total_g": total_g,
        "weighted_avg_per_100g": avg,
        "hits": [],   # _validate 里填
        "gaps": [],
    }


def _validate(plan: str, report: dict, items: list[SelectedItem], hard_rules: dict) -> None:
    """填充 hits / gaps 并在硬约束失败时抛错（规则来自 DB 的 hard_rules）"""
    avg = report["weighted_avg_per_100g"]
    hits: list[str] = []
    gaps: list[str] = []

    if "max_gi" in hard_rules:
        if avg["gi"] <= hard_rules["max_gi"]:
            hits.append(f"GI ≤ {hard_rules['max_gi']} ✓")
        else:
            gaps.append(f"GI {avg['gi']} 超出 {hard_rules['max_gi']}")

    if "max_sugar_g" in hard_rules:
        if avg["sugar_g"] <= hard_rules["max_sugar_g"]:
            hits.append(f"糖 ≤ {hard_rules['max_sugar_g']}g ✓")
        else:
            gaps.append(f"糖 {avg['sugar_g']}g 超出 {hard_rules['max_sugar_g']}g")

    if "min_fiber_g" in hard_rules:
        if avg["fiber_g"] >= hard_rules["min_fiber_g"]:
            hits.append(f"纤维 ≥ {hard_rules['min_fiber_g']}g ✓")
        else:
            gaps.append(f"纤维 {avg['fiber_g']}g 不足 {hard_rules['min_fiber_g']}g")

    if "min_folate_per_portion_ug" in hard_rules:
        folate_per_portion = (
            sum(it.nutrition_per_100g.get("folate_ug", 0) * 2 for it in items) / len(items)
            if items else 0
        )
        threshold = hard_rules["min_folate_per_portion_ug"]
        if folate_per_portion >= threshold:
            hits.append(f"每份叶酸 ≥ {threshold}µg ✓")
        else:
            gaps.append(f"每份叶酸 {folate_per_portion:.0f}µg 不足 {threshold}µg")

    if "min_vitC_mg" in hard_rules:
        if avg["vitC_mg"] >= hard_rules["min_vitC_mg"]:
            hits.append(f"维 C ≥ {hard_rules['min_vitC_mg']}mg ✓")
        else:
            gaps.append(f"维 C {avg['vitC_mg']}mg 不足 {hard_rules['min_vitC_mg']}mg")

    if "min_vitC_or_anthocyanin" in hard_rules:
        has_antho = any(it.nutrition_per_100g.get("anthocyanin_mg", 0) > 0 for it in items)
        if avg["vitC_mg"] >= 30 or has_antho:
            hits.append("抗氧维 C / 花青素 ✓")
        else:
            gaps.append("抗氧活性成分不足")

    report["hits"] = hits
    report["gaps"] = gaps

    if gaps:
        raise SelectionError(
            "PLAN_CONSTRAINT_FAILED",
            f"本周库存不足以满足方向硬约束：{gaps[0]}",
        )


class SelectionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
