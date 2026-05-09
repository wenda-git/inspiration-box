"""
健康方向静态目录 · 从 subscription_plans 表读。

返回字段保持前端已用的：
  code / name / tagline / price_cny / theme / emoji / bullets / first_payment_cny
bullets 从 hard_rules 自动推导出可读摘要，避免后台改硬约束后前端文案过期。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.services import catalog


router = APIRouter(prefix="/v1", tags=["plans"])


def _bullets_from_rules(hard_rules: dict) -> list[str]:
    """把 hard_rules 的 JSON 翻成中文卖点 bullets"""
    out: list[str] = []
    if isinstance(hard_rules, dict):
        rules = hard_rules
        # 孕产方向 by_stage 只显示 default
        if "by_stage" in rules or "default" in rules:
            rules = rules.get("default") or {}

        if "max_gi" in rules:
            out.append(f"加权 GI ≤ {rules['max_gi']}")
        if "max_sugar_g" in rules:
            out.append(f"糖 ≤ {rules['max_sugar_g']}g / 100g")
        if "min_fiber_g" in rules:
            out.append(f"膳食纤维 ≥ {rules['min_fiber_g']}g / 100g")
        if "min_folate_per_portion_ug" in rules:
            out.append(f"每份叶酸 ≥ {rules['min_folate_per_portion_ug']} µg")
        if "min_vitC_mg" in rules:
            out.append(f"每份维 C ≥ {rules['min_vitC_mg']} mg")
        if rules.get("min_vitC_or_anthocyanin"):
            out.append("维 C ≥ 30mg 或 花青素 ≥ 50mg")
    return out


@router.get("/plans")
async def list_plans() -> list[dict]:
    settings = get_settings()
    first_pay = float(settings.plan_first_payment_cny)

    plans = await catalog.load_plans()
    ordered = sorted(plans.values(), key=lambda p: p.code)
    return [
        {
            "code": p.code,
            "name": p.name,
            "tagline": p.tagline,
            "price_cny": float(p.price_cny),
            "first_payment_cny": first_pay,
            "theme": p.theme or "matcha",
            "emoji": p.emoji or "🍎",
            "bullets": _bullets_from_rules(p.hard_rules),
        }
        for p in ordered
    ]
