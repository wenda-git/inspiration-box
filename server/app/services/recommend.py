"""
LLM 文案生成器。

输入：已经选好的 items + 用户画像
输出：每个 item 的 reason + 整箱 guide_md

不做：选品、算数字、判断硬约束（那些 selector.py 全做了）
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.selector import SelectedItem


logger = logging.getLogger(__name__)

_PROMPT_PATHS = [
    Path(__file__).resolve().parents[2].parent / "prompts" / "write_copy.md",
    Path(__file__).resolve().parents[3] / "prompts" / "write_copy.md",
    Path("/prompts/write_copy.md"),
]
_DEFAULT_SYSTEM_PROMPT = """
你是「灵感果仓」的首席营养顾问。选品、配量、营养计算、预算全部由系统完成。
你的工作是为每个水果写一句具体原因、生成本周食用建议、生成一句温和的问候语，并排一份 7 天食用计划。

要求：
- 不允许修改任何水果、克重、价格、营养数字。
- 所有给用户看的水果名称必须使用中文名，不要输出 fruit_code。
- 语气像懂营养的朋友，克制、具体，不使用「亲」「哦」「呢」「家人们」「姐妹们」。
- 不要输出 emoji。
- reason 必须包含一个具体营养数字，并结合用户画像。
- daily_plan 必须覆盖周一到周日，每天用几个、几片、一小把这类自然表达，不强调精确克重。
- guide_md 使用 Markdown，包含最佳食用时段、每种水果吃法建议、小贴士。

严格输出 JSON：
{
  "items": [{"fruit_code": "xxx", "reason": "..."}],
  "guide_md": "...",
  "greeting": "...",
  "daily_plan": [{"day": "周一", "items": "...", "tip": "..."}]
}
"""


def _load_system_prompt() -> str:
    for path in _PROMPT_PATHS:
        if path.exists():
            return path.read_text(encoding="utf-8")
    logger.warning("write_copy prompt file not found; using built-in fallback prompt")
    return _DEFAULT_SYSTEM_PROMPT


_SYSTEM_PROMPT = _load_system_prompt()

_async_client = AsyncOpenAI()


def _build_input(
    plan_code: str,
    profile: dict,
    lifestyle: dict,
    items: list[SelectedItem],
    nutrition: dict,
    fruit_display: dict[str, dict] | None = None,
) -> str:
    """给 LLM 的精简输入 —— 包含画像 + 生活习惯 + 选品结果"""
    plan_label = {
        "fatloss_gi": "减脂控糖",
        "prenatal": "孕产营养",
        "antiox": "抗氧轻食",
    }.get(plan_code, plan_code)

    return json.dumps(
        {
            "plan_code": plan_code,
            "plan_label": plan_label,
            "user_profile": {
                "gender": lifestyle.get("gender"),
                "bmi": lifestyle.get("bmi"),
                "bmi_band": lifestyle.get("bmi_band"),
                "prenatal_stage": lifestyle.get("prenatal_stage"),
                "goals": profile.get("goals", []),
                "tags": profile.get("tags", []),
                "allergies": profile.get("allergies", []),
                "dislikes": profile.get("dislikes", []),
                "recent_negative_feedback": profile.get("recent_negative_feedback", []),
                # 生活习惯（关键：LLM 可以据此写个性化 greeting）
                "veg_freq": lifestyle.get("veg_freq"),
                "sleep_pattern": lifestyle.get("sleep_pattern"),
                "exercise_freq": lifestyle.get("exercise_freq"),
                "taste_prefer": lifestyle.get("taste_prefer", []),
            },
            "items": [
                {
                    "fruit_code": it.fruit_code,
                    "fruit_name_cn": (fruit_display or {}).get(it.fruit_code, {}).get("name_cn", it.fruit_code),
                    "qty_g": it.qty_g,
                    "nutrition_per_100g": it.nutrition_per_100g,
                }
                for it in items
            ],
            "nutrition_report": nutrition,
        },
        ensure_ascii=False,
    )


def _output_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items", "guide_md", "greeting", "daily_plan"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["fruit_code", "reason"],
                    "properties": {
                        "fruit_code": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "guide_md": {"type": "string"},
            "greeting": {"type": "string"},
            "daily_plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["day", "items", "tip"],
                    "properties": {
                        "day": {"type": "string"},
                        "items": {"type": "string"},
                        "tip": {"type": "string"},
                    },
                },
            },
        },
    }


async def write_copy(
    plan_code: str,
    profile: dict,
    lifestyle: dict,
    items: list[SelectedItem],
    nutrition: dict,
    fruit_display: dict[str, dict] | None = None,
) -> tuple[dict[str, str], str, str, list[dict], dict]:
    """
    返回 ({fruit_code: reason}, guide_md, greeting, daily_plan, meta)
    LLM 失败时回退到模板文案，保证接口不挂。
    """
    model = get_settings().openai_model
    user_payload = _build_input(plan_code, profile, lifestyle, items, nutrition, fruit_display)

    t0 = time.perf_counter()
    try:
        response = await _async_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "WeeklyCopy",
                    "strict": True,
                    "schema": _output_schema(),
                },
            },
            temperature=0.6,      # 文案需要一些变化
            max_tokens=1500,
        )
    except Exception as e:
        logger.warning("LLM copy failed, fallback to template: %s", e)
        return (
            _fallback_copy(items),
            _fallback_guide(plan_code, items, fruit_display),
            _fallback_greeting(),
            _fallback_daily_plan(items, fruit_display),
            {
                "model": model, "input_tokens": 0, "output_tokens": 0,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "trace_id": uuid4(), "stop_reason": "fallback",
                "cache_read_input_tokens": 0,
            },
        )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    data = json.loads(response.choices[0].message.content or "{}")

    reasons = {
        row["fruit_code"]: row["reason"]
        for row in data.get("items", [])
        if "fruit_code" in row and "reason" in row
    }
    for it in items:
        if it.fruit_code not in reasons:
            reasons[it.fruit_code] = _fallback_reason(it)

    guide_md = data.get("guide_md") or _fallback_guide(plan_code, items, fruit_display)
    greeting = data.get("greeting") or _fallback_greeting()
    daily_plan = data.get("daily_plan") or _fallback_daily_plan(items, fruit_display)

    usage = response.usage
    meta = {
        "trace_id": uuid4(),
        "model": model,
        "input_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
        "cache_read_input_tokens": (
            getattr(usage.prompt_tokens_details, "cached_tokens", 0) if usage else 0
        ),
        "latency_ms": latency_ms,
        "stop_reason": response.choices[0].finish_reason,
    }
    return reasons, guide_md, greeting, daily_plan, meta


# ---------- 模板兜底（LLM 失败时用） ----------

def _fallback_reason(it: SelectedItem) -> str:
    n = it.nutrition_per_100g
    gi = n.get("gi", 0)
    fiber = n.get("fiber_g", 0)
    vitc = n.get("vitC_mg", 0)
    pieces = []
    if gi and gi <= 45:
        pieces.append(f"GI {gi} 偏低")
    if fiber and fiber >= 2:
        pieces.append(f"纤维 {fiber}g/100g")
    if vitc and vitc >= 20:
        pieces.append(f"维 C {vitc}mg/100g")
    return "、".join(pieces) + "，适合这周的补给。"


def _fallback_copy(items: list[SelectedItem]) -> dict[str, str]:
    return {it.fruit_code: _fallback_reason(it) for it in items}


def _display_name(it: SelectedItem, fruit_display: dict[str, dict] | None) -> str:
    return (fruit_display or {}).get(it.fruit_code, {}).get("name_cn") or it.fruit_code


def _serving_tip(name: str, nutrition: dict | None = None) -> str:
    nutrition = nutrition or {}
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
    if (nutrition.get("fiber_g") or 0) >= 3:
        return "纤维比较友好，放在早餐后或下午吃更稳"
    if (nutrition.get("vitC_mg") or 0) >= 30:
        return "维 C 比较足，切开后尽快吃，别久放"
    return "分 2-3 次吃更舒服，冷藏后口感更好"


def _fallback_guide(
    plan_code: str,
    items: list[SelectedItem],
    fruit_display: dict[str, dict] | None = None,
) -> str:
    names = [_display_name(it, fruit_display) for it in items]
    lines = ["## 本周食用建议", ""]
    lines.append("**最佳时段**：放在早餐后或下午加餐更合适，尽量避免睡前 2 小时集中吃。")
    lines.append("")
    for it, name in zip(items, names):
        lines.append(f"- **{name}**：{_serving_tip(name, it.nutrition_per_100g)}。")
    lines.append("")
    lines.append("**小贴士**：熟度高和切开的水果优先吃；苹果、番石榴这类更耐放的可以留到后半周。")
    return "\n".join(lines)


def _fallback_greeting() -> str:
    return "这周为你挑了一箱匹配你画像的水果，慢慢享用。——灵感果仓"


def _fallback_daily_plan(
    items: list[SelectedItem],
    fruit_display: dict[str, dict] | None = None,
) -> list[dict]:
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    names = [_display_name(it, fruit_display) for it in items] or ["本周水果"]
    tips = [
        "早餐后吃，肠胃更舒服",
        "下午加餐，替代甜点",
        "冷藏后口感更清爽",
        "切开后尽快吃完",
        "搭配无糖酸奶更稳",
        "运动后少量补充",
        "睡前两小时尽量不吃",
    ]
    plan = []
    for idx, day in enumerate(days):
        first = names[idx % len(names)]
        second = names[(idx + 2) % len(names)] if len(names) > 2 and idx % 2 == 0 else ""
        item_text = f"{first} + {second}" if second and second != first else first
        plan.append({"day": day, "items": item_text, "tip": tips[idx]})
    return plan
