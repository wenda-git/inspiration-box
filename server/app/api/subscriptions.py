"""
订阅 · 带支付版

状态机：
  unpaid  → (首单支付成功) → trial
  trial   → (本周已发货，次周即将扣费) → active
  active  → (周四扣费成功) → active（循环）
  active/trial → (用户取消) → cancelled
  active  → (扣费失败) → overdue

选择健康方向后 subscriptions.billing_state='unpaid'，见到配单后点"立即订阅" → /pay → 成功后变 trial
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import get_pool
from app.deps import current_user_id
from app.integrations import wxpay


router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])


VALID_PLANS = {"fatloss_gi", "prenatal", "antiox"}
# 未终止状态：用于"是否已有活跃订阅"判断
OPEN_STATUSES = ("trial", "active", "paused", "unpaid")


class SubscriptionIn(BaseModel):
    plan_code: str
    address_id: UUID | None = None


class SubscriptionAddressIn(BaseModel):
    address_id: UUID


class Subscription(BaseModel):
    id: UUID
    plan_code: str
    status: str
    billing_state: str                 # unpaid | paid | cancelled | overdue
    address_id: UUID | None
    started_at: datetime
    cancelled_at: datetime | None
    first_paid_at: datetime | None
    next_charge_at: datetime | None
    first_payment_cny: float | None
    recurring_cny: float | None
    charge_count: int
    created_at: datetime
    paused_at: datetime | None = None
    pause_until: date | None = None
    pause_reason: str | None = None


def _row_to_sub(row) -> Subscription:
    return Subscription(
        id=row["id"],
        plan_code=row["plan_code"],
        status=row["status"],
        billing_state=row["billing_state"] or "unpaid",
        address_id=row["address_id"],
        started_at=row["started_at"],
        cancelled_at=row["cancelled_at"],
        first_paid_at=row.get("first_paid_at") if hasattr(row, "get") else row["first_paid_at"],
        next_charge_at=row["next_charge_at"],
        first_payment_cny=float(row["first_payment_cny"]) if row["first_payment_cny"] is not None else None,
        recurring_cny=float(row["recurring_cny"]) if row["recurring_cny"] is not None else None,
        charge_count=int(row["charge_count"] or 0),
        created_at=row["created_at"],
        paused_at=row["paused_at"],
        pause_until=row["pause_until"],
        pause_reason=row["pause_reason"],
    )


SELECT_COLS = """
    id, plan_code, status, billing_state, address_id,
    started_at, cancelled_at, first_paid_at, next_charge_at,
    first_payment_cny, recurring_cny, charge_count, created_at,
    wx_contract_id, paused_at, pause_until, pause_reason
"""


# ---------- 查当前订阅 ----------

@router.get("/current", response_model=Subscription | None)
async def get_current(
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {SELECT_COLS} FROM subscriptions "
            "WHERE user_id = $1 AND status = ANY($2::text[]) "
            "ORDER BY created_at DESC LIMIT 1",
            UUID(user_id_str), list(OPEN_STATUSES),
        )
    return _row_to_sub(row) if row else None


# ---------- 创建订阅（只选健康方向，不付钱） ----------

@router.post("", response_model=Subscription, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: SubscriptionIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription:
    if body.plan_code not in VALID_PLANS:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_PLAN", "message": f"未知健康方向: {body.plan_code}"},
        )

    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # 如果已有未付的订阅 → 复用（用户换方向）
        existing = await conn.fetchrow(
            """
            SELECT id FROM subscriptions
             WHERE user_id = $1 AND status = ANY($2::text[])
             ORDER BY created_at DESC LIMIT 1
            """,
            user_id, list(OPEN_STATUSES),
        )
        if existing:
            # 已订阅 + 已付费 → 拒绝
            paid = await conn.fetchval(
                "SELECT billing_state FROM subscriptions WHERE id = $1", existing["id"]
            )
            if paid and paid not in ("unpaid", "cancelled"):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "SUBSCRIPTION_EXISTS", "message": "你已有一个进行中的订阅，取消后再订新的。"},
                )
            # 未付的可以改方向
            row = await conn.fetchrow(
                f"""
                UPDATE subscriptions SET
                  plan_code = $2,
                  status = 'unpaid',
                  billing_state = 'unpaid',
                  updated_at = now()
                WHERE id = $1
                RETURNING {SELECT_COLS}
                """,
                existing["id"], body.plan_code,
            )
            return _row_to_sub(row)

        # 地址
        address_id = body.address_id
        if address_id is not None:
            owned = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM addresses WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL)",
                address_id, user_id,
            )
            if not owned:
                raise HTTPException(status_code=400, detail={"code": "INVALID_ADDRESS"})
        else:
            address_id = await conn.fetchval(
                "SELECT id FROM addresses WHERE user_id = $1 AND is_default = TRUE AND deleted_at IS NULL LIMIT 1",
                user_id,
            )

        # 创建为 unpaid 状态
        row = await conn.fetchrow(
            f"""
            INSERT INTO subscriptions
                (user_id, plan_code, address_id, status, billing_state)
            VALUES ($1, $2, $3, 'unpaid', 'unpaid')
            RETURNING {SELECT_COLS}
            """,
            user_id, body.plan_code, address_id,
        )
    return _row_to_sub(row)


# ---------- 首单支付 ----------

class PayResult(BaseModel):
    payment_id: UUID
    out_trade_no: str
    prepay_id: str
    amount_cny: float
    provider: str                # mock | real
    auto_success: bool           # mock 模式下 true，前端不调 wx.requestPayment 直接当成功
    payment_params: dict | None = None  # real 模式下透传给 wx.requestPayment
    subscription: Subscription
    order: dict | None = None


class PauseIn(BaseModel):
    weeks: int = Field(default=1, ge=1, le=8)
    reason: str | None = Field(default=None, max_length=200)


@router.post("/{sub_id}/pay", response_model=PayResult)
async def start_first_payment(
    sub_id: UUID,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> PayResult:
    """
    发起首单支付。
    mock 模式：直接把 subscriptions 标成 trial + 写 payments + 回 auto_success=True
    real 模式：生成 prepay_id 给前端去调 wx.requestPayment，后端回调改状态
    """
    user_id = UUID(user_id_str)
    settings = get_settings()
    pool = await get_pool()

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            f"SELECT {SELECT_COLS}, user_id FROM subscriptions WHERE id = $1 AND user_id = $2",
            sub_id, user_id,
        )

    if sub is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
    if sub["billing_state"] not in ("unpaid", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail={"code": "ALREADY_PAID", "message": "此订阅已付款"},
        )

    async with pool.acquire() as conn:
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
                address_id, sub_id,
            )

    amount = settings.plan_first_payment_cny
    # 后续每周扣 plan.price_cny，先从 subscription_plans 查
    async with pool.acquire() as conn:
        recurring = await conn.fetchval(
            "SELECT price_cny FROM subscription_plans WHERE code = $1", sub["plan_code"]
        )
    recurring = float(recurring or 128)

    out_trade_no = wxpay.new_out_trade_no()
    try:
        intent = await wxpay.place_first_order(
            user_id=str(user_id),
            out_trade_no=out_trade_no,
            amount_cny=amount,
            description=f"灵感果仓 首单 · {sub['plan_code']}",
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail={"code": "PAYMENT_NOT_CONFIGURED", "message": "微信支付暂未配置，请先使用 mock 支付或配置商户参数"},
        )

    # 写 payments 流水
    order = None
    async with pool.acquire() as conn, conn.transaction():
        payment_id = await conn.fetchval(
            """
            INSERT INTO payments
                (user_id, subscription_id, kind, amount_cny, status,
                 idempotency_key, wx_prepay_id)
            VALUES ($1, $2, 'first', $3, 'pending', $4, $5)
            RETURNING id
            """,
            user_id, sub_id, amount, out_trade_no, intent.prepay_id,
        )

        # mock 模式：直接标成功
        if intent.auto_success:
            now = datetime.now(timezone.utc)
            next_charge = now + timedelta(days=7)
            contract_id = wxpay.new_contract_id()
            await conn.execute(
                """
                UPDATE payments SET
                  status = 'success',
                  wx_tx_id = $2,
                  paid_at = now(),
                  updated_at = now()
                WHERE id = $1
                """,
                payment_id, f"mock_tx_{payment_id.hex[:16]}",
            )
            await conn.execute(
                """
                UPDATE subscriptions SET
                  status = 'trial',
                  billing_state = 'paid',
                  wx_contract_id = $2,
                  first_paid_at = now(),
                  first_payment_cny = $3,
                  recurring_cny = $4,
                  next_charge_at = $5,
                  started_at = CASE WHEN billing_state = 'unpaid' THEN now() ELSE started_at END,
                  charge_count = 1,
                  last_charge_at = now(),
                  updated_at = now()
                WHERE id = $1
                """,
                sub_id, contract_id, amount, recurring, next_charge,
            )
            from app.api.orders import create_current_order_for_paid_subscription
            order_out = await create_current_order_for_paid_subscription(conn, user_id)
            order = order_out.model_dump(mode="json")

        updated = await conn.fetchrow(
            f"SELECT {SELECT_COLS} FROM subscriptions WHERE id = $1", sub_id
        )

    return PayResult(
        payment_id=payment_id,
        out_trade_no=out_trade_no,
        prepay_id=intent.prepay_id,
        amount_cny=amount,
        provider=intent.provider,
        auto_success=intent.auto_success,
        payment_params=None,
        subscription=_row_to_sub(updated),
        order=order,
    )


# ---------- 取消订阅 ----------

@router.post("/{sub_id}/cancel", response_model=Subscription)
async def cancel_subscription(
    sub_id: UUID,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {SELECT_COLS} FROM subscriptions
             WHERE id = $1 AND user_id = $2 AND status = ANY($3::text[])
            """,
            sub_id, user_id, list(OPEN_STATUSES),
        )
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})

        # 已付款的订阅要终止微信合同
        if row["wx_contract_id"]:
            try:
                await wxpay.terminate_contract(row["wx_contract_id"])
            except Exception:
                # mock 不会抛；real 失败先不阻塞取消，让运维去对账
                pass

        updated = await conn.fetchrow(
            f"""
            UPDATE subscriptions SET
              status = 'cancelled',
              billing_state = 'cancelled',
              cancelled_at = now(),
              next_charge_at = NULL,
              updated_at = now()
            WHERE id = $1
            RETURNING {SELECT_COLS}
            """,
            sub_id,
        )
    return _row_to_sub(updated)


@router.post("/{sub_id}/pause", response_model=Subscription)
async def pause_subscription(
    sub_id: UUID,
    body: PauseIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription:
    user_id = UUID(user_id_str)
    until = (datetime.now(timezone.utc) + timedelta(days=7 * body.weeks)).date()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE subscriptions
               SET status = 'paused',
                   paused_at = now(),
                   pause_until = $3,
                   pause_reason = $4,
                   next_charge_at = GREATEST(COALESCE(next_charge_at, now()), $3::timestamptz),
                   updated_at = now()
             WHERE id = $1
               AND user_id = $2
               AND status IN ('trial', 'active')
               AND billing_state = 'paid'
            RETURNING {SELECT_COLS}
            """,
            sub_id, user_id, until, body.reason,
        )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "没有可暂停的订阅"})
    return _row_to_sub(row)


@router.post("/{sub_id}/resume", response_model=Subscription)
async def resume_subscription(
    sub_id: UUID,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE subscriptions
               SET status = CASE WHEN charge_count <= 1 THEN 'trial' ELSE 'active' END,
                   paused_at = NULL,
                   pause_until = NULL,
                   pause_reason = NULL,
                   updated_at = now()
             WHERE id = $1
               AND user_id = $2
               AND status = 'paused'
               AND billing_state = 'paid'
            RETURNING {SELECT_COLS}
            """,
            sub_id, user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "没有可恢复的订阅"})
    return _row_to_sub(row)


@router.post("/{sub_id}/plan", response_model=Subscription)
async def change_subscription_plan(
    sub_id: UUID,
    body: SubscriptionIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription:
    if body.plan_code not in VALID_PLANS:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PLAN", "message": f"未知健康方向: {body.plan_code}"})
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE subscriptions
               SET plan_code = $3,
                   recurring_cny = COALESCE((SELECT price_cny FROM subscription_plans WHERE code = $3), recurring_cny),
                   updated_at = now()
             WHERE id = $1
               AND user_id = $2
               AND status = ANY($4::text[])
            RETURNING {SELECT_COLS}
            """,
            sub_id, user_id, body.plan_code, list(OPEN_STATUSES),
        )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
    return _row_to_sub(row)


# ---------- 换地址 ----------

@router.post("/{sub_id}/address", response_model=Subscription)
async def change_subscription_address(
    sub_id: UUID,
    body: SubscriptionAddressIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Subscription:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        owned = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM addresses WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL)",
            body.address_id, user_id,
        )
        if not owned:
            raise HTTPException(status_code=400, detail={"code": "INVALID_ADDRESS"})
        row = await conn.fetchrow(
            f"""
            UPDATE subscriptions SET address_id = $1, updated_at = now()
             WHERE id = $2 AND user_id = $3 AND status = ANY($4::text[])
            RETURNING {SELECT_COLS}
            """,
            body.address_id, sub_id, user_id, list(OPEN_STATUSES),
        )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
    return _row_to_sub(row)
