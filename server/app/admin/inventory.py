"""
/admin/inventory · 库存批次管理
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import require_admin
from app.db import get_pool
from app.services import catalog as catalog_svc


router = APIRouter(prefix="/admin/inventory", tags=["admin-inventory"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("", response_class=HTMLResponse)
async def list_batches(
    request: Request,
    _: str = Depends(require_admin),
    q: str = "",
    status: str = "active",
    risk: str = "all",
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.id, b.batch_no, b.fruit_code, f.name_cn, f.emoji,
                   b.warehouse_code, b.qty_total_g, b.qty_avail_g,
                   b.unit_cost_cny_per_kg, b.arrived_on, b.best_before,
                   b.qc_metrics, b.is_active,
                   COALESCE(sum(r.qty_g) FILTER (WHERE r.status = 'reserved'), 0) AS reserved_g,
                   (b.best_before - current_date) AS days_left
              FROM inventory_batches b
              LEFT JOIN fruits f ON f.code = b.fruit_code
              LEFT JOIN order_inventory_reservations r ON r.batch_no = b.batch_no
             GROUP BY b.id, f.name_cn, f.emoji
             ORDER BY b.is_active DESC, b.best_before ASC
            """
        )
        stats = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE is_active AND qty_avail_g > 0) AS active,
              COUNT(*) FILTER (WHERE qty_avail_g <= 0) AS soldout,
              COUNT(*) FILTER (WHERE NOT is_active) AS inactive,
              COUNT(*) FILTER (WHERE is_active AND qty_avail_g > 0 AND best_before <= current_date + 4) AS expiring,
              COALESCE(sum(qty_avail_g) FILTER (WHERE is_active AND qty_avail_g > 0), 0) AS avail_g
              FROM inventory_batches
            """
        )
    all_rows = [dict(r) for r in rows]
    q_norm = q.strip().lower()
    filtered = []
    for r in all_rows:
        if q_norm:
            hay = " ".join(str(r.get(k) or "") for k in ("fruit_code", "name_cn", "batch_no", "warehouse_code")).lower()
            if q_norm not in hay:
                continue
        if status == "active" and not (r["is_active"] and r["qty_avail_g"] > 0):
            continue
        if status == "soldout" and r["qty_avail_g"] > 0:
            continue
        if status == "inactive" and r["is_active"]:
            continue
        if risk == "expiring" and not (r["is_active"] and r["qty_avail_g"] > 0 and r["days_left"] <= 4):
            continue
        filtered.append(r)
    return templates.TemplateResponse(request, "inventory_list.html", {
        "rows": filtered,
        "stats": dict(stats) if stats else {},
        "today": date.today(),
        "q": q,
        "status": status,
        "risk": risk,
    })


@router.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        fruits = await conn.fetch(
            "SELECT code, name_cn, emoji FROM fruits WHERE is_active ORDER BY name_cn"
        )
    default_best = (date.today() + timedelta(days=10)).isoformat()
    return templates.TemplateResponse(request, "inventory_form.html", {
        "item": None,
        "fruits": [dict(f) for f in fruits],
        "today": date.today().isoformat(),
        "default_best": default_best,
        "title": "新增库存批次",
    })


@router.post("/new")
async def create_batch(request: Request, _: str = Depends(require_admin)):
    form = await request.form()
    data = _parse_form(form)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO inventory_batches
                    (fruit_code, batch_no, warehouse_code,
                     qty_total_g, qty_avail_g, unit_cost_cny_per_kg,
                     arrived_on, best_before, qc_metrics, is_active)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                """,
                data["fruit_code"], data["batch_no"], data["warehouse_code"],
                data["qty_total_g"], data["qty_avail_g"], data["unit_cost_cny_per_kg"],
                data["arrived_on"], data["best_before"],
                json.dumps(data["qc_metrics"]), data["is_active"],
            )
        except Exception as e:
            pool_fruits = await get_pool()
            async with pool_fruits.acquire() as c2:
                fruits = await c2.fetch(
                    "SELECT code, name_cn, emoji FROM fruits WHERE is_active ORDER BY name_cn"
                )
            return templates.TemplateResponse(request, "inventory_form.html", {
                "item": data, "error": str(e),
                "fruits": [dict(f) for f in fruits],
                "today": date.today().isoformat(),
                "default_best": (date.today() + timedelta(days=10)).isoformat(),
                "title": "新增库存批次",
            }, status_code=400)
    catalog_svc.invalidate("inventory")
    return RedirectResponse("/admin/inventory", status_code=303)


@router.get("/{batch_id}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, batch_id: str, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM inventory_batches WHERE id = $1", batch_id
        )
        fruits = await conn.fetch(
            "SELECT code, name_cn, emoji FROM fruits WHERE is_active ORDER BY name_cn"
        )
    if row is None:
        raise HTTPException(404)
    item = dict(row)
    if isinstance(item.get("qc_metrics"), str):
        item["qc_metrics"] = json.loads(item["qc_metrics"])
    item["qc_metrics_extra_json"] = json.dumps(
        _extra_qc_metrics(item.get("qc_metrics") or {}),
        ensure_ascii=False,
    )
    item["arrived_on"] = item["arrived_on"].isoformat()
    item["best_before"] = item["best_before"].isoformat()
    return templates.TemplateResponse(request, "inventory_form.html", {
        "item": item,
        "fruits": [dict(f) for f in fruits],
        "today": date.today().isoformat(),
        "default_best": item["best_before"],
        "title": f"编辑批次 {item['batch_no']}",
    })


@router.post("/{batch_id}/edit")
async def update_batch(request: Request, batch_id: str, _: str = Depends(require_admin)):
    form = await request.form()
    data = _parse_form(form)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE inventory_batches SET
                fruit_code=$2, batch_no=$3, warehouse_code=$4,
                qty_total_g=$5, qty_avail_g=$6, unit_cost_cny_per_kg=$7,
                arrived_on=$8, best_before=$9, qc_metrics=$10::jsonb, is_active=$11,
                updated_at = now()
            WHERE id = $1
            """,
            batch_id,
            data["fruit_code"], data["batch_no"], data["warehouse_code"],
            data["qty_total_g"], data["qty_avail_g"], data["unit_cost_cny_per_kg"],
            data["arrived_on"], data["best_before"],
            json.dumps(data["qc_metrics"]), data["is_active"],
        )
    catalog_svc.invalidate("inventory")
    return RedirectResponse("/admin/inventory", status_code=303)


@router.post("/{batch_id}/toggle")
async def toggle(request: Request, batch_id: str, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE inventory_batches SET is_active = NOT is_active, updated_at = now() WHERE id = $1",
            batch_id,
        )
    catalog_svc.invalidate("inventory")
    return RedirectResponse("/admin/inventory", status_code=303)


def _parse_form(form) -> dict:
    def _int(k, d=0):
        v = form.get(k)
        try: return int(float(v)) if v not in (None, "") else d
        except: return d

    def _num(k, d=0.0):
        v = form.get(k)
        try: return float(v) if v not in (None, "") else d
        except: return d

    def _date(k, d=None):
        v = form.get(k)
        if not v: return d
        try: return date.fromisoformat(v)
        except: return d

    raw_qc = (form.get("qc_metrics_json") or "").strip() or "{}"
    try:
        qc = json.loads(raw_qc)
        if not isinstance(qc, dict): qc = {}
    except: qc = {}
    for form_key, qc_key in (
        ("qc_brix", "brix"),
        ("qc_damage_rate", "damage_rate"),
        ("qc_size_mm", "size_mm"),
    ):
        if form.get(form_key) not in (None, ""):
            qc[qc_key] = _num(form_key)

    qty_total = _int("qty_total_g")
    qty_avail = _int("qty_avail_g", qty_total)
    return {
        "fruit_code": (form.get("fruit_code") or "").strip(),
        "batch_no":   (form.get("batch_no") or "").strip(),
        "warehouse_code": (form.get("warehouse_code") or "").strip() or None,
        "qty_total_g": qty_total,
        "qty_avail_g": min(qty_avail, qty_total) if qty_total else qty_avail,
        "unit_cost_cny_per_kg": _num("unit_cost_cny_per_kg"),
        "arrived_on": _date("arrived_on", date.today()),
        "best_before": _date("best_before", date.today() + timedelta(days=10)),
        "qc_metrics": qc,
        "qc_metrics_extra_json": json.dumps(_extra_qc_metrics(qc), ensure_ascii=False),
        "is_active": form.get("is_active") == "on",
    }


def _extra_qc_metrics(qc: dict) -> dict:
    return {
        k: v for k, v in (qc or {}).items()
        if k not in {"brix", "damage_rate", "size_mm"}
    }
