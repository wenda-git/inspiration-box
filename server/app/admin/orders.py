"""
/admin/orders · 本周配单汇总 + 订单履约
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import require_admin
from app.db import get_pool


router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

ORDER_STATUSES = {"locked", "packing", "shipped", "delivered", "cancelled"}
STATUS_LABELS = {
    "locked": "已锁单",
    "packing": "分拣中",
    "shipped": "已发货",
    "delivered": "已签收",
    "cancelled": "已取消",
}


async def _reserve_inventory_for_order(conn, order_id, items: list[dict]) -> None:
    existing = await conn.fetchval(
        "SELECT count(*) FROM order_inventory_reservations WHERE order_id = $1",
        order_id,
    )
    if existing:
        return
    for it in items:
        batch_no = it.get("batch_id")
        fruit_code = it.get("fruit_code")
        qty_g = int(it.get("qty_g") or 0)
        if not batch_no or not fruit_code or qty_g <= 0:
            continue
        updated = await conn.execute(
            """
            UPDATE inventory_batches
               SET qty_avail_g = qty_avail_g - $2, updated_at = now()
             WHERE batch_no = $1 AND qty_avail_g >= $2 AND is_active
            """,
            batch_no, qty_g,
        )
        if not updated.endswith(" 1"):
            raise RuntimeError(f"库存不足: {batch_no}")
        await conn.execute(
            """
            INSERT INTO order_inventory_reservations
                (order_id, batch_no, fruit_code, qty_g, status)
            VALUES ($1, $2, $3, $4, 'reserved')
            ON CONFLICT (order_id, batch_no, fruit_code) DO NOTHING
            """,
            order_id, batch_no, fruit_code, qty_g,
        )


async def _release_reservations(conn, order_id) -> None:
    rows = await conn.fetch(
        """
        SELECT id, batch_no, qty_g
          FROM order_inventory_reservations
         WHERE order_id = $1 AND status = 'reserved'
        """,
        order_id,
    )
    for r in rows:
        await conn.execute(
            """
            UPDATE inventory_batches
               SET qty_avail_g = qty_avail_g + $2, updated_at = now()
             WHERE batch_no = $1
            """,
            r["batch_no"], int(r["qty_g"]),
        )
        await conn.execute(
            """
            UPDATE order_inventory_reservations
               SET status = 'released', updated_at = now()
             WHERE id = $1
            """,
            r["id"],
        )


async def _finalize_reservations(conn, order_id) -> None:
    await conn.execute(
        """
        UPDATE order_inventory_reservations
           SET status = 'deducted', updated_at = now()
         WHERE order_id = $1 AND status = 'reserved'
        """,
        order_id,
    )


@router.get("", response_class=HTMLResponse)
async def summary(
    request: Request,
    _: str = Depends(require_admin),
    status: str = "open",
    plan: str = "",
    q: str = "",
):
    iso_year, iso_week, _d = datetime.now(timezone.utc).isocalendar()
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 本周配单列表 + 付款状态
        rows = await conn.fetch(
            """
            SELECT w.id, w.user_id, u.phone_e164, w.plan_code, sp.name AS plan_name,
                   w.total_cost_cny, w.model, w.tokens_in, w.tokens_out, w.latency_ms,
                   w.created_at, w.payload, sp.price_cny, sp.budget_cny, sp.soft_prefs,
                   COALESCE(s.billing_state, 'unknown') AS billing_state,
                   s.first_payment_cny, s.recurring_cny, s.charge_count
              FROM weekly_recommendations w
              LEFT JOIN users u ON u.id = w.user_id
              LEFT JOIN subscription_plans sp ON sp.code = w.plan_code
              LEFT JOIN subscriptions s ON s.id = w.subscription_id
             WHERE w.iso_year = $1 AND w.iso_week = $2
             ORDER BY w.created_at DESC
            """,
            iso_year, iso_week,
        )

        # 按健康方向汇总
        stats = await conn.fetch(
            """
            SELECT w.plan_code, count(*) AS orders,
                   COALESCE(round(sum(total_cost_cny)::numeric, 2), 0) AS total_cost,
                   COALESCE(round(avg(total_cost_cny)::numeric, 2), 0) AS avg_cost,
                   COALESCE(round(avg(COALESCE(s.recurring_cny, sp.price_cny))::numeric, 2), 0) AS avg_price,
                   COALESCE(round(avg(COALESCE((sp.soft_prefs->>'estimated_ops_cost_cny')::numeric, 0))::numeric, 2), 0) AS avg_ops_cost,
                   COALESCE(round(sum(COALESCE(s.recurring_cny, sp.price_cny) - w.total_cost_cny - COALESCE((sp.soft_prefs->>'estimated_ops_cost_cny')::numeric, 0))::numeric, 2), 0) AS contribution
              FROM weekly_recommendations w
              LEFT JOIN subscription_plans sp ON sp.code = w.plan_code
              LEFT JOIN subscriptions s ON s.id = w.subscription_id
             WHERE w.iso_year = $1 AND w.iso_week = $2
             GROUP BY w.plan_code
             ORDER BY orders DESC
            """,
            iso_year, iso_week,
        )

        # 真实收入（payments 表）
        revenue = await conn.fetchrow(
            """
            SELECT
              COALESCE(sum(amount_cny) FILTER (WHERE kind='first'     AND status='success'), 0) AS first_rev,
              COALESCE(sum(amount_cny) FILTER (WHERE kind='recurring' AND status='success'), 0) AS recur_rev,
              COUNT(*) FILTER (WHERE status='success')  AS paid_count,
              COUNT(*) FILTER (WHERE status='pending')  AS pending_count
              FROM payments
             WHERE created_at > now() - interval '7 days'
            """
        )
        # 订阅状态汇总
        sub_stats = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE billing_state='paid')      AS paid,
              COUNT(*) FILTER (WHERE billing_state='unpaid')    AS unpaid,
              COUNT(*) FILTER (WHERE billing_state='cancelled') AS cancelled
              FROM subscriptions
            """
        )
        fulfilment_stats = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE status = 'locked') AS locked,
              COUNT(*) FILTER (WHERE status = 'packing') AS packing,
              COUNT(*) FILTER (WHERE status = 'shipped') AS shipped,
              COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
              COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
              FROM orders
             WHERE created_at > now() - interval '30 days'
            """
        )

        clauses = []
        args = []
        if status == "open":
            clauses.append("o.status IN ('locked','packing','shipped')")
        elif status in ORDER_STATUSES:
            args.append(status)
            clauses.append(f"o.status = ${len(args)}")
        if plan:
            args.append(plan)
            clauses.append(f"o.plan_code = ${len(args)}")
        if q:
            args.append(f"%{q}%")
            clauses.append(f"u.phone_e164 ILIKE ${len(args)}")
        where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
        orders = await conn.fetch(
            f"""
            SELECT o.id, o.iso_year, o.iso_week, o.plan_code, o.status,
                   o.items_snapshot, o.total_cost_cny, o.carrier_code, o.tracking_no,
                   o.locked_at, o.shipped_at, o.delivered_at, o.auto_locked, u.phone_e164,
                   sp.price_cny, sp.soft_prefs, s.recurring_cny,
                   COALESCE(sum(r.qty_g) FILTER (WHERE r.status = 'reserved'), 0) AS reserved_g
              FROM orders o
              LEFT JOIN users u ON u.id = o.user_id
              LEFT JOIN subscription_plans sp ON sp.code = o.plan_code
              LEFT JOIN subscriptions s ON s.id = o.subscription_id
              LEFT JOIN order_inventory_reservations r ON r.order_id = o.id
             {where_sql}
             GROUP BY o.id, u.phone_e164, sp.price_cny, sp.soft_prefs, s.recurring_cny
             ORDER BY o.iso_year DESC, o.iso_week DESC, o.created_at DESC
             LIMIT 120
            """,
            *args,
        )

    # 把 payload 里的 items 变成可渲染的简写
    items = []
    for r in rows:
        d = dict(r)
        payload = d["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        lst = (payload or {}).get("items", [])
        d["items_short"] = " · ".join(
            f"{it.get('emoji','')}{it.get('fruit_name_cn','?')} {it.get('qty_g',0)}g"
            for it in lst[:5]
        )
        d["item_count"] = len(lst)
        d["phone_masked"] = _mask(d["phone_e164"])
        d["at"] = d["created_at"].strftime("%m-%d %H:%M")
        _add_margin_fields(d)
        items.append(d)

    fulfilments = []
    for r in orders:
        d = dict(r)
        snapshot = d["items_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        snapshot = snapshot or []
        d["items_short"] = " · ".join(
            f"{it.get('emoji','')}{it.get('fruit_name_cn','?')} {it.get('qty_g',0)}g"
            for it in snapshot[:4]
        )
        d["phone_masked"] = _mask(d["phone_e164"])
        d["reserved_g"] = int(d["reserved_g"] or 0)
        d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
        _add_margin_fields(d)
        fulfilments.append(d)

    return templates.TemplateResponse(request, "orders.html", {
        "iso_year": iso_year,
        "iso_week": iso_week,
        "rows": items,
        "stats": [dict(s) for s in stats],
        "revenue": dict(revenue) if revenue else {},
        "sub_stats": dict(sub_stats) if sub_stats else {},
        "fulfilment_stats": dict(fulfilment_stats) if fulfilment_stats else {},
        "fulfilments": fulfilments,
        "order_statuses": ["locked", "packing", "shipped", "delivered", "cancelled"],
        "status_labels": STATUS_LABELS,
        "filters": {"status": status, "plan": plan, "q": q},
    })


@router.post("/run-cycle")
async def run_cycle(
    _: str = Depends(require_admin),
    action: str = Form(...),
):
    iso_year, iso_week, _d = datetime.now(timezone.utc).isocalendar()
    pool = await get_pool()
    async with pool.acquire() as conn:
        if action == "lock_paid":
            subs = await conn.fetch(
                """
                SELECT s.id AS subscription_id, s.user_id, s.plan_code, s.address_id,
                       w.id AS recommendation_id, w.payload, w.total_cost_cny
                  FROM subscriptions s
                  JOIN weekly_recommendations w
                    ON w.user_id = s.user_id
                   AND w.iso_year = $1
                   AND w.iso_week = $2
                 WHERE s.billing_state = 'paid'
                   AND s.status IN ('trial', 'active')
                   AND NOT EXISTS (
                     SELECT 1 FROM orders o
                      WHERE o.user_id = s.user_id AND o.iso_year = $1 AND o.iso_week = $2
                   )
                """,
                iso_year, iso_week,
            )
            for sub in subs:
                async with conn.transaction():
                    address_id = sub["address_id"]
                    if address_id is None:
                        address_id = await conn.fetchval(
                            "SELECT id FROM addresses WHERE user_id = $1 AND is_default = TRUE AND deleted_at IS NULL LIMIT 1",
                            sub["user_id"],
                        )
                    if address_id is None:
                        # 运营后台不应生成无法配送的订单；用户下次进入支付/锁单会被引导补地址。
                        continue
                    payload = sub["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    items = (payload or {}).get("items", [])
                    row = await conn.fetchrow(
                        """
                        INSERT INTO orders
                            (user_id, subscription_id, recommendation_id, address_id,
                             iso_year, iso_week, plan_code, status, items_snapshot,
                             total_cost_cny, auto_locked)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, 'locked', $8::jsonb, $9, TRUE)
                        RETURNING id
                        """,
                        sub["user_id"], sub["subscription_id"], sub["recommendation_id"], address_id,
                        iso_year, iso_week, sub["plan_code"],
                        json.dumps(items, ensure_ascii=False),
                        float(sub["total_cost_cny"] or 0),
                    )
                    await _reserve_inventory_for_order(conn, row["id"], items)
        elif action == "packing_locked":
            await conn.execute(
                """
                UPDATE orders
                   SET status = 'packing', updated_at = now()
                 WHERE iso_year = $1 AND iso_week = $2 AND status = 'locked'
                """,
                iso_year, iso_week,
            )
        elif action == "mark_trial_active":
            await conn.execute(
                """
                UPDATE subscriptions
                   SET status = 'active', updated_at = now()
                 WHERE status = 'trial' AND billing_state = 'paid'
                """
            )
        else:
            raise HTTPException(status_code=422, detail="invalid action")
    from app.services import catalog as _catalog
    _catalog.invalidate("inventory")
    return RedirectResponse("/admin/orders", status_code=303)


@router.post("/{order_id}/status")
async def update_order_status(
    order_id: UUID,
    _: str = Depends(require_admin),
    status: str = Form(...),
    carrier_code: str | None = Form(None),
    tracking_no: str | None = Form(None),
):
    if status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail="invalid status")

    carrier = (carrier_code or "").strip() or None
    tracking = (tracking_no or "").strip() or None
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if status == "cancelled":
            await _release_reservations(conn, order_id)
        elif status in ("shipped", "delivered"):
            await _finalize_reservations(conn, order_id)

        row = await conn.fetchrow(
            """
            UPDATE orders
               SET status = $2,
                   carrier_code = COALESCE($3, carrier_code),
                   tracking_no = COALESCE($4, tracking_no),
                   shipped_at = CASE
                     WHEN $2 = 'shipped' AND shipped_at IS NULL THEN now()
                     WHEN $2 IN ('locked', 'packing', 'cancelled') THEN NULL
                     ELSE shipped_at
                   END,
                   delivered_at = CASE
                     WHEN $2 = 'delivered' AND delivered_at IS NULL THEN now()
                     WHEN $2 IN ('locked', 'packing', 'shipped', 'cancelled') THEN NULL
                     ELSE delivered_at
                   END,
                   updated_at = now()
             WHERE id = $1
             RETURNING id
            """,
            order_id, status, carrier, tracking,
        )
    if not row:
        raise HTTPException(status_code=404, detail="order not found")
    from app.services import catalog as _catalog
    _catalog.invalidate("inventory")
    return RedirectResponse("/admin/orders", status_code=303)


def _mask(p: str | None) -> str:
    if not p: return "-"
    t = p[-11:]
    return t[:3] + "****" + t[-4:]


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _add_margin_fields(row: dict) -> None:
    soft = _json_dict(row.get("soft_prefs"))
    price = _float(row.get("recurring_cny") or row.get("price_cny"))
    fruit_cost = _float(row.get("total_cost_cny"))
    ops_cost = _float(soft.get("estimated_ops_cost_cny"))
    margin = price - fruit_cost - ops_cost
    row["ops_cost_cny"] = round(ops_cost, 2)
    row["margin_base_price_cny"] = round(price, 2)
    row["estimated_margin_cny"] = round(margin, 2)
    row["estimated_margin_pct"] = round(margin / price * 100, 1) if price else 0
    row["fruit_cost_pct"] = round(fruit_cost / price * 100, 1) if price else 0
    row["margin_state"] = (
        "bad" if margin < 0 or row["fruit_cost_pct"] > 55
        else "warn" if row["estimated_margin_pct"] < 20
        else "good"
    )
