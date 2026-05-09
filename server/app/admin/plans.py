"""
/admin/plans · 箱型策略编辑
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import require_admin
from app.db import get_pool
from app.services import catalog as catalog_svc


router = APIRouter(prefix="/admin/plans", tags=["admin-plans"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

THEME_OPTIONS = [
    ("matcha", "抹茶绿"),
    ("berry", "莓果紫"),
    ("peach", "蜜桃橙"),
]


@router.get("", response_class=HTMLResponse)
async def list_plans(request: Request, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT code, name, tagline, theme, emoji, price_cny, budget_cny,
                   box_target_g, hard_rules, soft_prefs, is_active, sort_order
              FROM subscription_plans
             ORDER BY sort_order
            """
        )
    return templates.TemplateResponse(request, "plans_list.html", {
        "rows": [_format_row(r) for r in rows],
    })


@router.get("/{code}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, code: str, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM subscription_plans WHERE code = $1", code
        )
    if row is None:
        raise HTTPException(404)
    item = _format_row(row)
    return templates.TemplateResponse(request, "plan_form.html", {
        "item": item,
        "title": f"编辑箱型策略 {item['name']}",
        "theme_options": THEME_OPTIONS,
    })


@router.post("/{code}/edit")
async def update_plan(request: Request, code: str, _: str = Depends(require_admin)):
    form = await request.form()

    def _num(k, d=0.0):
        v = form.get(k)
        try: return float(v) if v not in (None, "") else d
        except: return d

    hard_extra = {}
    try:
        hard_extra = json.loads((form.get("hard_rules_extra") or "").strip() or "{}")
        if not isinstance(hard_extra, dict):
            hard_extra = {}
    except Exception as e:
        raise HTTPException(400, f"高级营养规则不合法: {e}")

    hard = _rules_from_form(form, hard_extra, code)

    soft_extra = {}
    try:
        soft_extra = json.loads((form.get("soft_prefs_extra") or "").strip() or "{}")
        if not isinstance(soft_extra, dict):
            soft_extra = {}
    except Exception as e:
        raise HTTPException(400, f"高级配单规则不合法: {e}")

    soft = {**soft_extra, **_soft_from_form(form)}

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE subscription_plans SET
                name=$2, tagline=$3, theme=$4, emoji=$5,
                price_cny=$6, budget_cny=$7, box_target_g=$8,
                hard_rules=$9::jsonb, soft_prefs=$10::jsonb,
                is_active=$11, sort_order=$12, updated_at=now()
            WHERE code=$1
            """,
            code,
            (form.get("name") or "").strip(),
            (form.get("tagline") or "").strip() or None,
            (form.get("theme") or "matcha").strip(),
            (form.get("emoji") or "🍎").strip(),
            _num("price_cny"), _num("budget_cny"),
            int(_num("box_target_g", 2500)),
            json.dumps(hard),
            json.dumps(soft),
            form.get("is_active") == "on",
            int(_num("sort_order", 0)),
        )
    catalog_svc.invalidate("plans")
    return RedirectResponse("/admin/plans", status_code=303)


def _format_row(row) -> dict:
    item = dict(row)
    hard = item.get("hard_rules")
    soft = item.get("soft_prefs")
    if isinstance(hard, str): hard = json.loads(hard)
    if isinstance(soft, str): soft = json.loads(soft)
    hard = hard or {}
    soft = soft or {}
    price = _float(item.get("price_cny"))
    budget = _float(item.get("budget_cny"))
    ops_cost = _float(soft.get("estimated_ops_cost_cny"))
    contribution = price - budget - ops_cost
    item["fruit_cost_pct"] = round(budget / price * 100, 1) if price else 0
    item["estimated_ops_cost_cny"] = round(ops_cost, 2)
    item["estimated_contribution_cny"] = round(contribution, 2)
    item["estimated_contribution_pct"] = round(contribution / price * 100, 1) if price else 0
    item["margin_state"] = (
        "bad" if contribution < 0 or item["fruit_cost_pct"] > 55
        else "warn" if item["estimated_contribution_pct"] < 20
        else "good"
    )
    item["hard_rules_json"] = json.dumps(hard or {}, ensure_ascii=False, indent=2)
    item["soft_prefs_json"] = json.dumps(soft, ensure_ascii=False, indent=2)
    item["hard_rules_summary"] = _hard_rules_summary(hard)
    item["hard_rules"] = hard
    item["soft_prefs"] = soft
    item["theme_label"] = dict(THEME_OPTIONS).get(item.get("theme"), item.get("theme") or "")
    item["hard_form"] = hard.get("default") if isinstance(hard.get("default"), dict) else hard
    item["first_box"] = soft.get("first_box") if isinstance(soft.get("first_box"), dict) else {}
    item["stage_rules_json"] = json.dumps(hard.get("by_stage") or {}, ensure_ascii=False, indent=2)
    item["hard_extra_json"] = json.dumps(_extra_hard_rules(hard), ensure_ascii=False, indent=2)
    item["soft_extra_json"] = json.dumps(_extra_soft_prefs(soft), ensure_ascii=False, indent=2)
    return item


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _hard_rules_summary(hard: dict) -> list[str]:
    if not isinstance(hard, dict):
        return []
    base = hard.get("default") if isinstance(hard.get("default"), dict) else hard
    if not isinstance(base, dict):
        return []
    labels = {
        "max_gi": "GI 不高于",
        "max_sugar_g": "每 100g 糖不高于",
        "min_fiber_g": "每 100g 纤维不低于",
        "min_folate_per_portion_ug": "单份叶酸不低于",
        "min_vitC_mg": "每 100g 维 C 不低于",
    }
    units = {
        "max_gi": "",
        "max_sugar_g": "g",
        "min_fiber_g": "g",
        "min_folate_per_portion_ug": "µg",
        "min_vitC_mg": "mg",
    }
    out = []
    for key, label in labels.items():
        if key in base:
            out.append(f"{label} {base[key]}{units[key]}")
    if base.get("min_vitC_or_anthocyanin"):
        out.append("维 C 或花青素需命中其一")
    return out


def _present(form, key: str) -> bool:
    return form.get(key) not in (None, "")


def _num_field(form, key: str):
    if not _present(form, key):
        return None
    try:
        return float(form.get(key))
    except (TypeError, ValueError):
        return None


def _int_field(form, key: str, default: int = 0) -> int:
    try:
        return int(float(form.get(key)))
    except (TypeError, ValueError):
        return default


def _rules_from_form(form, extra: dict, code: str) -> dict:
    base = dict(extra or {})
    for key in (
        "max_gi",
        "max_sugar_g",
        "min_fiber_g",
        "min_folate_per_portion_ug",
        "min_vitC_mg",
    ):
        value = _num_field(form, key)
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    if form.get("min_vitC_or_anthocyanin") == "on":
        base["min_vitC_or_anthocyanin"] = True
    else:
        base.pop("min_vitC_or_anthocyanin", None)

    if code == "prenatal":
        try:
            by_stage = json.loads((form.get("stage_rules_json") or "").strip() or "{}")
            if not isinstance(by_stage, dict):
                by_stage = {}
        except Exception:
            by_stage = {}
        if by_stage:
            return {"default": base, "by_stage": by_stage}
    return base


def _soft_from_form(form) -> dict:
    return {
        "min_items": _int_field(form, "min_items", 3),
        "max_items": _int_field(form, "max_items", 5),
        "single_share_max": _float(form.get("single_share_max"), 0.38),
        "target_fill_ratio": _float(form.get("target_fill_ratio"), 0.82),
        "max_total_ratio": _float(form.get("max_total_ratio"), 1.05),
        "premium_unit_cost_cny_per_kg": _float(form.get("premium_unit_cost_cny_per_kg"), 65),
        "max_premium_items": _int_field(form, "max_premium_items", 1),
        "max_highlight_items": _int_field(form, "max_highlight_items", 3),
        "estimated_ops_cost_cny": _float(form.get("estimated_ops_cost_cny"), 0),
        "target_contribution_margin": _float(form.get("target_contribution_margin"), 0.2),
        "first_box": {
            "min_items": _int_field(form, "first_min_items", 4),
            "max_items": _int_field(form, "first_max_items", 5),
            "box_target_g": _int_field(form, "first_box_target_g", 2000),
            "budget_cny": _float(form.get("first_budget_cny"), 90),
            "single_share_max": _float(form.get("first_single_share_max"), 0.34),
            "target_fill_ratio": _float(form.get("first_target_fill_ratio"), 0.82),
            "max_total_ratio": _float(form.get("first_max_total_ratio"), 1.08),
            "max_premium_items": _int_field(form, "first_max_premium_items", 1),
        },
    }


def _extra_hard_rules(hard: dict) -> dict:
    if not isinstance(hard, dict):
        return {}
    base = hard.get("default") if isinstance(hard.get("default"), dict) else hard
    known = {
        "max_gi", "max_sugar_g", "min_fiber_g",
        "min_folate_per_portion_ug", "min_vitC_mg",
        "min_vitC_or_anthocyanin", "default", "by_stage",
    }
    return {k: v for k, v in base.items() if k not in known}


def _extra_soft_prefs(soft: dict) -> dict:
    known = {
        "min_items", "max_items", "single_share_max", "target_fill_ratio",
        "max_total_ratio", "premium_unit_cost_cny_per_kg", "max_premium_items",
        "max_highlight_items", "estimated_ops_cost_cny", "target_contribution_margin",
        "first_box",
    }
    return {k: v for k, v in (soft or {}).items() if k not in known}
