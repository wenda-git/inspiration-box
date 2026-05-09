"""
/v1/me/* · 当前用户的聚合状态，小程序登录后路由决策用。
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.db import get_pool
from app.deps import current_user_id

router = APIRouter(prefix="/v1/me", tags=["me"])


@router.get("/onboarding-status")
async def onboarding_status(
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> dict:
    """返回当前用户聚合状态，前端据此决定登录后去哪一页。"""
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        has_profile = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM health_profiles WHERE user_id = $1 AND is_current)",
            user_id,
        )
        has_address = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM addresses WHERE user_id = $1 AND deleted_at IS NULL)",
            user_id,
        )
        # 看用户是否选过健康方向 —— 不管付款了没
        has_subscription = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM subscriptions
                 WHERE user_id = $1 AND status IN ('trial','active','paused','unpaid')
            )
            """,
            user_id,
        )
        # 付款才算"完成订阅"
        has_paid_subscription = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM subscriptions
                 WHERE user_id = $1 AND billing_state = 'paid'
                   AND status IN ('trial','active','paused')
            )
            """,
            user_id,
        )
    # C 方案：问卷里选方向（含 plan_code） → 画像保存 → weekly。
    # 地址后置到支付/锁单前补齐，避免新用户还没看配单就被地址表单打断。
    if not has_profile:
        next_step = "profile"
    elif not has_subscription:
        next_step = "profile"
    else:
        next_step = "weekly"

    # 当前周订单状态（给前端判断"购买后状态卡"怎么展示）
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    order_status = None
    async with pool.acquire() as conn:
        order_status = await conn.fetchval(
            """
            SELECT status FROM orders
             WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
             LIMIT 1
            """,
            user_id, iso_year, iso_week,
        )

    return {
        "has_profile": bool(has_profile),
        "has_address": bool(has_address),
        "has_subscription": bool(has_subscription),
        "has_paid_subscription": bool(has_paid_subscription),
        "current_order_status": order_status,   # null | locked | packing | shipped | delivered | cancelled
        "next_step": next_step,
    }
