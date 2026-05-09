"""
/admin/fruits · 水果主档 CRUD（运营能在此添加/修改水果的营养、产地、时令等）
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.admin.deps import require_admin
from app.db import get_pool
from app.services import catalog as catalog_svc


router = APIRouter(prefix="/admin/fruits", tags=["admin-fruits"])

templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


# 营养字段列表（和 DB 对应）
NUTRITION_FIELDS = [
    ("kcal",           "热量 kcal"),
    ("carb_g",         "碳水 g"),
    ("sugar_g",        "糖 g"),
    ("fructose_g",     "果糖 g"),
    ("fiber_g",        "膳食纤维 g"),
    ("protein_g",      "蛋白质 g"),
    ("fat_g",          "脂肪 g"),
    ("gi",             "GI 血糖指数"),
    ("vitC_mg",        "维 C mg"),
    ("folate_ug",      "叶酸 µg"),
    ("anthocyanin_mg", "花青素 mg"),
    ("potassium_mg",   "钾 mg"),
    ("magnesium_mg",   "镁 mg"),
    ("calcium_mg",     "钙 mg"),
    ("iron_mg",        "铁 mg"),
    ("orac_umol_te",   "ORAC 抗氧化值"),
]

CATEGORIES = ["浆果", "柑橘", "核果", "仁果", "瓜果", "热带", "其他"]

SERVING_UNITS = [
    ("g", "按克"),
    ("piece", "按个"),
    ("box", "按盒"),
    ("half_kg", "按 500g"),
]

ORIGIN_REGIONS = [
    ("south_china", "南方产区"),
    ("north_china", "北方产区"),
    ("imported",    "进口"),
    ("",            "未指定"),
]

VALUE_TIERS = [
    ("basic", "基础"),
    ("highlight", "亮点"),
    ("premium", "精品"),
]

SELECTION_STATUSES = [
    ("normal", "正常"),
    ("featured", "本周主推"),
    ("downranked", "本周降权"),
    ("paused", "暂停选品"),
]

MONTH_OPTIONS = [(i, f"{i}月") for i in range(1, 13)]

ALLERGY_TAG_OPTIONS = [
    ("kiwi", "奇异果 / 猕猴桃"),
    ("citrus", "柑橘类"),
    ("berry", "莓果类"),
    ("strawberry", "草莓"),
    ("latex", "乳胶相关"),
    ("mango", "芒果"),
    ("passionfruit", "百香果"),
    ("pomegranate", "石榴"),
]

COMMON_TAG_OPTIONS = [
    ("low_gi", "低 GI"),
    ("low_sugar", "低糖"),
    ("high_fiber", "高纤维"),
    ("high_vitC", "高维 C"),
    ("high_folate", "高叶酸"),
    ("high_anthocyanin", "高花青素"),
    ("high_potassium", "高钾"),
    ("premium", "精品"),
    ("seasonal", "当季"),
]

DIRECTION_OPTIONS = [
    ("fatloss_gi", "控糖轻盈"),
    ("antiox", "熬夜抗氧"),
    ("prenatal", "孕产温和"),
]

AFFINITY_OPTIONS = [
    (-60, "强降权"),
    (-30, "降权"),
    (0, "普通"),
    (15, "略优先"),
    (25, "优先"),
    (30, "高优先"),
    (40, "强优先"),
    (60, "本方向核心"),
]


def _form_context(**extra):
    context = {
        "nutrition_fields": NUTRITION_FIELDS,
        "categories": CATEGORIES,
        "serving_units": SERVING_UNITS,
        "origin_regions": ORIGIN_REGIONS,
        "value_tiers": VALUE_TIERS,
        "selection_statuses": SELECTION_STATUSES,
        "month_options": MONTH_OPTIONS,
        "allergy_tag_options": ALLERGY_TAG_OPTIONS,
        "common_tag_options": COMMON_TAG_OPTIONS,
        "direction_options": DIRECTION_OPTIONS,
        "affinity_options": AFFINITY_OPTIONS,
    }
    context.update(extra)
    return context


# ---------- 列表 ----------

@router.get("", response_class=HTMLResponse)
async def list_fruits(request: Request, _: str = Depends(require_admin), q: str = ""):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if q:
            rows = await conn.fetch(
                """
                SELECT code, name_cn, emoji, category, gi, sugar_g, fiber_g,
                       origin, season_months, allergy_tags, value_tier,
                       selection_status, ops_weight, is_active
                  FROM fruits
                 WHERE name_cn ILIKE $1 OR code ILIKE $1
                 ORDER BY is_active DESC, name_cn
                """,
                f"%{q}%",
            )
        else:
            rows = await conn.fetch(
                """
                SELECT code, name_cn, emoji, category, gi, sugar_g, fiber_g,
                       origin, season_months, allergy_tags, value_tier,
                       selection_status, ops_weight, is_active
                  FROM fruits
                 ORDER BY is_active DESC, name_cn
                """
            )
    return templates.TemplateResponse(request, "fruits_list.html", {
        "rows": [dict(r) for r in rows],
        "q": q,
    })


# ---------- 新增 ----------

@router.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "fruit_form.html", _form_context(
        item=None,
        title="新增水果",
    ))


@router.post("/new")
async def create_fruit(request: Request, _: str = Depends(require_admin)):
    form = await request.form()
    data = _parse_form(form)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(f"""
                INSERT INTO fruits (code, name_cn, name_en, emoji, category,
                    {", ".join(k for k, _ in NUTRITION_FIELDS)},
                    source, allergy_tags, value_tier, selection_status, ops_weight, ops_note,
                    plan_affinity, forbidden_for, tags, is_active,
                    serving_unit, serving_size_g,
                    origin, origin_region, season_months)
                VALUES (
                    $1,$2,$3,$4,$5,
                    $6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,
                    $22,$23::text[],$24,$25,$26,$27,
                    $28::jsonb,$29::text[],$30::text[],$31,
                    $32,$33,$34,$35,$36::int[]
                )
            """,
                data["code"], data["name_cn"], data["name_en"], data["emoji"], data["category"],
                *[data["nutrition"].get(k, 0) for k, _ in NUTRITION_FIELDS],
                data["source"],
                data["allergy_tags"], data["value_tier"], data["selection_status"],
                data["ops_weight"], data["ops_note"],
                json.dumps(data["plan_affinity"]),
                data["forbidden_for"],
                data["tags"],
                data["is_active"],
                data["serving_unit"], data["serving_size_g"],
                data["origin"], data["origin_region"], data["season_months"],
            )
        except Exception as e:
            return templates.TemplateResponse(request, "fruit_form.html", _form_context(
                item=data,
                error=str(e),
                title="新增水果",
            ), status_code=400)
    catalog_svc.invalidate("fruits")
    catalog_svc.invalidate("inventory")
    return RedirectResponse("/admin/fruits", status_code=303)


# ---------- 编辑 ----------

@router.get("/{code}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, code: str, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM fruits WHERE code = $1", code)
    if row is None:
        raise HTTPException(404)
    item = dict(row)
    # jsonb 要解码
    if isinstance(item.get("plan_affinity"), str):
        item["plan_affinity"] = json.loads(item["plan_affinity"])
    item["plan_affinity_json"] = json.dumps(item.get("plan_affinity") or {}, ensure_ascii=False)
    for key in ("fatloss_gi", "antiox", "prenatal"):
        item[f"affinity_{key}"] = int((item.get("plan_affinity") or {}).get(key, 0) or 0)
    item["forbidden_for"] = list(item.get("forbidden_for") or [])
    item["tags"] = list(item.get("tags") or [])
    known_tags = {code for code, _ in COMMON_TAG_OPTIONS}
    item["extra_tags"] = ",".join([tag for tag in item["tags"] if tag not in known_tags])
    item["allergy_tags"] = list(item.get("allergy_tags") or [])
    item["season_months"] = list(item.get("season_months") or [])
    item["nutrition"] = {k: float(item.get(k) or 0) for k, _ in NUTRITION_FIELDS}
    return templates.TemplateResponse(request, "fruit_form.html", _form_context(
        item=item,
        title=f"编辑 {item['name_cn']}",
    ))


@router.post("/{code}/edit")
async def update_fruit(request: Request, code: str, _: str = Depends(require_admin)):
    form = await request.form()
    data = _parse_form(form)

    set_clauses = [
        "name_cn=$2", "name_en=$3", "emoji=$4", "category=$5",
    ]
    args = [code, data["name_cn"], data["name_en"], data["emoji"], data["category"]]
    i = 6
    for k, _label in NUTRITION_FIELDS:
        set_clauses.append(f"{k}=${i}")
        args.append(data["nutrition"].get(k, 0))
        i += 1
    set_clauses.extend([
        f"source=${i}", f"allergy_tags=${i+1}::text[]",
        f"value_tier=${i+2}", f"selection_status=${i+3}", f"ops_weight=${i+4}",
        f"ops_note=${i+5}",
        f"plan_affinity=${i+6}::jsonb",
        f"forbidden_for=${i+7}::text[]", f"tags=${i+8}::text[]",
        f"is_active=${i+9}",
        f"serving_unit=${i+10}", f"serving_size_g=${i+11}",
        f"origin=${i+12}", f"origin_region=${i+13}", f"season_months=${i+14}::int[]",
        "updated_at = now()",
    ])
    args.extend([
        data["source"],
        data["allergy_tags"], data["value_tier"], data["selection_status"],
        data["ops_weight"], data["ops_note"],
        json.dumps(data["plan_affinity"]),
        data["forbidden_for"],
        data["tags"],
        data["is_active"],
        data["serving_unit"], data["serving_size_g"],
        data["origin"], data["origin_region"], data["season_months"],
    ])

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE fruits SET {', '.join(set_clauses)} WHERE code=$1", *args)
    catalog_svc.invalidate("fruits")
    catalog_svc.invalidate("inventory")
    return RedirectResponse("/admin/fruits", status_code=303)


# ---------- 下架（软删） ----------

@router.post("/{code}/toggle")
async def toggle_active(request: Request, code: str, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE fruits SET is_active = NOT is_active, updated_at = now() WHERE code = $1",
            code,
        )
    catalog_svc.invalidate("fruits")
    catalog_svc.invalidate("inventory")
    return RedirectResponse("/admin/fruits", status_code=303)


# ---------- 解析表单 ----------

def _parse_form(form) -> dict:
    def _num(key: str, default: float = 0.0) -> float:
        v = form.get(key)
        if v is None or v == "":
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def _split(key: str) -> list[str]:
        raw_values: list[str] = []
        if hasattr(form, "getlist"):
            raw_values = [str(v).strip() for v in form.getlist(key) if str(v).strip()]
        if raw_values:
            values: list[str] = []
            for raw in raw_values:
                values.extend(s.strip() for s in raw.replace("，", ",").split(",") if s.strip())
            return values
        v = form.get(key, "")
        return [s.strip() for s in str(v).replace("，", ",").split(",") if s.strip()]

    def _split_int(key: str) -> list[int]:
        out = []
        for s in _split(key):
            try:
                n = int(s)
                if 1 <= n <= 12:
                    out.append(n)
            except ValueError:
                pass
        return sorted(set(out))

    plan_aff = {}
    for key in ("fatloss_gi", "antiox", "prenatal"):
        val = _num(f"affinity_{key}", 0)
        if val:
            plan_aff[key] = int(val)

    raw_affinity = (form.get("plan_affinity_json") or "").strip()
    if raw_affinity:
        try:
            extra_aff = json.loads(raw_affinity)
            if isinstance(extra_aff, dict):
                for extra_key, extra_value in extra_aff.items():
                    if extra_key not in {"fatloss_gi", "antiox", "prenatal"}:
                        plan_aff[extra_key] = extra_value
        except Exception:
            pass

    value_tiers = {code for code, _ in VALUE_TIERS}
    selection_statuses = {code for code, _ in SELECTION_STATUSES}
    serving_units = {code for code, _ in SERVING_UNITS}
    origin_regions = {code for code, _ in ORIGIN_REGIONS}
    tags = sorted(set(_split("tags") + _split("extra_tags")))

    return {
        "code": (form.get("code") or "").strip(),
        "name_cn": (form.get("name_cn") or "").strip(),
        "name_en": (form.get("name_en") or "").strip() or None,
        "emoji": (form.get("emoji") or "").strip() or "🍎",
        "category": (form.get("category") or "").strip(),
        "nutrition": {k: _num(f"n_{k}") for k, _ in NUTRITION_FIELDS},
        "source": (form.get("source") or "").strip() or None,
        "allergy_tags": sorted(set(_split("allergy_tags"))),
        "value_tier": (form.get("value_tier") or "basic").strip() if (form.get("value_tier") or "basic").strip() in value_tiers else "basic",
        "selection_status": (form.get("selection_status") or "normal").strip() if (form.get("selection_status") or "normal").strip() in selection_statuses else "normal",
        "ops_weight": int(_num("ops_weight", 0)),
        "ops_note": (form.get("ops_note") or "").strip() or None,
        "plan_affinity": plan_aff,
        "plan_affinity_json": json.dumps(plan_aff, ensure_ascii=False),
        "affinity_fatloss_gi": int(plan_aff.get("fatloss_gi", 0) or 0),
        "affinity_antiox": int(plan_aff.get("antiox", 0) or 0),
        "affinity_prenatal": int(plan_aff.get("prenatal", 0) or 0),
        "forbidden_for": _split("forbidden_for"),
        "tags": tags,
        "extra_tags": ",".join([tag for tag in tags if tag not in {code for code, _ in COMMON_TAG_OPTIONS}]),
        "is_active": form.get("is_active") == "on",
        "serving_unit": (form.get("serving_unit") or "g").strip() if (form.get("serving_unit") or "g").strip() in serving_units else "g",
        "serving_size_g": int(_num("serving_size_g", 500)),
        "origin": (form.get("origin") or "").strip() or None,
        "origin_region": (form.get("origin_region") or "").strip() if (form.get("origin_region") or "").strip() in origin_regions else None,
        "season_months": _split_int("season_months"),
    }
