"""
/v1/orders · 订单 / 锁单 / 收货状态

MVP 阶段（P3 周四自动发货未上线前）只实现"锁单"：
  - POST /v1/orders/lock  把当前周的配单 snapshot 写成 orders(status=locked)
  - GET  /v1/orders/current  当前周的订单状态（没有则返回 null）
  - GET  /v1/orders  订单历史
  - GET  /v1/orders/{order_id} 订单详情
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import current_user_id


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/orders", tags=["orders"])


# ---------- schemas ----------

class OrderOut(BaseModel):
    id: UUID
    iso_year: int
    iso_week: int
    plan_code: str
    status: str
    items_snapshot: list
    total_cost_cny: float
    carrier_code: str | None = None
    tracking_no: str | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    locked_at: datetime
    created_at: datetime


class LockIn(BaseModel):
    # 允许前端明确传 year/week；不传则用当前周
    iso_year: int | None = None
    iso_week: int | None = None


# ---------- helpers ----------

def _parse_jsonb(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def _row_to_order(row) -> OrderOut:
    return OrderOut(
        id=row["id"],
        iso_year=row["iso_year"],
        iso_week=row["iso_week"],
        plan_code=row["plan_code"],
        status=row["status"],
        items_snapshot=_parse_jsonb(row["items_snapshot"]) or [],
        total_cost_cny=float(row["total_cost_cny"]),
        carrier_code=row["carrier_code"],
        tracking_no=row["tracking_no"],
        shipped_at=row["shipped_at"],
        delivered_at=row["delivered_at"],
        locked_at=row["locked_at"],
        created_at=row["created_at"],
    )


SELECT_COLS = """
    id, iso_year, iso_week, plan_code, status, items_snapshot,
    total_cost_cny, carrier_code, tracking_no,
    shipped_at, delivered_at, locked_at, created_at
"""


async def _reserve_inventory_for_order(conn, order_id: UUID, items: list[dict]) -> None:
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
             WHERE batch_no = $1
               AND qty_avail_g >= $2
               AND is_active
            """,
            batch_no, qty_g,
        )
        if not updated.endswith(" 1"):
            raise HTTPException(
                status_code=409,
                detail={"code": "INVENTORY_INSUFFICIENT", "message": "本周部分水果库存已不足，请换一批后再确认"},
            )

        await conn.execute(
            """
            INSERT INTO order_inventory_reservations
                (order_id, batch_no, fruit_code, qty_g, status)
            VALUES ($1, $2, $3, $4, 'reserved')
            ON CONFLICT (order_id, batch_no, fruit_code) DO NOTHING
            """,
            order_id, batch_no, fruit_code, qty_g,
        )

    from app.services import catalog as _catalog
    _catalog.invalidate("inventory")


async def create_current_order_for_paid_subscription(
    conn,
    user_id: UUID,
    *,
    iso_year: int | None = None,
    iso_week: int | None = None,
) -> OrderOut:
    """
    支付成功后自动生成本周订单。

    这是用户侧的主路径：钱付成功，就应该立刻有订单。/orders/lock 仅保留为兼容入口。
    """
    now = datetime.now(timezone.utc)
    current_year, current_week, _ = now.isocalendar()
    iso_year = iso_year or current_year
    iso_week = iso_week or current_week

    existing = await conn.fetchrow(
        f"""
        SELECT {SELECT_COLS} FROM orders
         WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
         LIMIT 1
        """,
        user_id, iso_year, iso_week,
    )
    if existing:
        return _row_to_order(existing)

    sub = await conn.fetchrow(
        """
        SELECT id, billing_state, address_id, status FROM subscriptions
         WHERE user_id = $1 AND status IN ('trial','active','unpaid')
         ORDER BY created_at DESC LIMIT 1
        """,
        user_id,
    )
    if sub is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "SUBSCRIPTION_REQUIRED", "message": "请先选择健康方向"},
        )
    if sub["billing_state"] != "paid":
        raise HTTPException(
            status_code=422,
            detail={"code": "PAYMENT_REQUIRED", "message": "请先完成首单支付"},
        )

    address_id = sub["address_id"]
    if address_id is None:
        address_id = await conn.fetchval(
            "SELECT id FROM addresses WHERE user_id = $1 AND is_default = TRUE AND deleted_at IS NULL LIMIT 1",
            user_id,
        )
        if address_id is None:
            address_id = await conn.fetchval(
                "SELECT id FROM addresses WHERE user_id = $1 AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
                user_id,
            )
        if address_id is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "ADDRESS_REQUIRED", "message": "请先填写收货地址"},
            )
        await conn.execute(
            "UPDATE subscriptions SET address_id = $1, updated_at = now() WHERE id = $2",
            address_id, sub["id"],
        )

    rec = await conn.fetchrow(
        """
        SELECT id, plan_code, payload, total_cost_cny
          FROM weekly_recommendations
         WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
         LIMIT 1
        """,
        user_id, iso_year, iso_week,
    )
    if rec is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "NO_RECOMMENDATION", "message": "本周还没生成配单"},
        )

    payload = _parse_jsonb(rec["payload"]) or {}
    items = payload.get("items", [])
    payment_id = await conn.fetchval(
        """
        SELECT id FROM payments
         WHERE user_id = $1 AND status = 'success'
         ORDER BY created_at DESC LIMIT 1
        """,
        user_id,
    )

    row = await conn.fetchrow(
        f"""
        INSERT INTO orders
            (user_id, subscription_id, recommendation_id, payment_id, address_id,
             iso_year, iso_week, plan_code, status,
             items_snapshot, total_cost_cny)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'locked',
                $9::jsonb, $10)
        RETURNING {SELECT_COLS}
        """,
        user_id, sub["id"], rec["id"], payment_id, address_id,
        iso_year, iso_week, rec["plan_code"],
        json.dumps(items, ensure_ascii=False),
        float(rec["total_cost_cny"]),
    )
    await _reserve_inventory_for_order(conn, row["id"], items)
    return _row_to_order(row)


# ---------- endpoints ----------

@router.get("/current", response_model=OrderOut | None)
async def get_current_order(
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> OrderOut | None:
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {SELECT_COLS} FROM orders
             WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
             LIMIT 1
            """,
            UUID(user_id_str), iso_year, iso_week,
        )
    if row:
        return _row_to_order(row)

    # 兼容旧流程：已付款但还没有订单时，首次查看自动补建。
    try:
        async with pool.acquire() as conn, conn.transaction():
            return await create_current_order_for_paid_subscription(
                conn, UUID(user_id_str), iso_year=iso_year, iso_week=iso_week,
            )
    except HTTPException:
        return None


@router.get("", response_model=list[OrderOut])
async def list_orders(
    user_id_str: Annotated[str, Depends(current_user_id)],
    limit: int = 20,
) -> list[OrderOut]:
    limit = max(1, min(limit, 50))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {SELECT_COLS} FROM orders
             WHERE user_id = $1
             ORDER BY iso_year DESC, iso_week DESC
             LIMIT $2
            """,
            UUID(user_id_str), limit,
        )
    return [_row_to_order(r) for r in rows]


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: UUID,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> OrderOut:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {SELECT_COLS} FROM orders
             WHERE id = $1 AND user_id = $2
             LIMIT 1
            """,
            order_id, UUID(user_id_str),
        )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在"})
    return _row_to_order(row)


@router.post("/lock", response_model=OrderOut)
async def lock_current_order(
    body: LockIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> OrderOut:
    """
    把当前周的配单"锁单"：
      - 必须已付款（subscriptions.billing_state = 'paid'）
      - 从 weekly_recommendations 拷贝 snapshot 到 orders
      - 已有 order → 幂等返回（不重复创建）
    """
    user_id = UUID(user_id_str)
    now = datetime.now(timezone.utc)
    iso_year = body.iso_year or now.isocalendar()[0]
    iso_week = body.iso_week or now.isocalendar()[1]

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        order = await create_current_order_for_paid_subscription(
            conn, user_id, iso_year=iso_year, iso_week=iso_week,
        )
    return order
