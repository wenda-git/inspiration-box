"""
微信支付 + 顺丰轨迹 · 外部回调入口

这些端点由外部系统直接调用（微信/顺丰 → 我们），不走用户 JWT 鉴权。
签名/解密在 integrations.wxpay.verify_and_decrypt_notify 里做。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.integrations import shunfeng, wxpay


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["webhooks"])


@router.post("/wxpay/notify")
async def wxpay_notify(request: Request) -> dict:
    raw = await request.body()
    event = await wxpay.verify_and_decrypt_notify(dict(request.headers), raw)
    if event is None:
        # mock 模式没有真实回调；real 模式返回 None 说明验签失败
        return {"code": "SUCCESS", "message": "OK"}

    # TODO: 接入 payments 表
    #   - TRANSACTION.SUCCESS: 按 out_trade_no 更新 payments.status = success
    #     + subscriptions 标 paid / 推进 next_charge_at
    #   - CONTRACT.TERMINATED: subscriptions.status = cancelled
    logger.info("wxpay event=%s", event.get("event_type"))
    return {"code": "SUCCESS", "message": "OK"}


@router.post("/shunfeng/routes")
async def sf_routes_callback(payload: dict) -> dict:
    """顺丰轨迹推送（订阅式）"""
    for evt in payload.get("events", []):
        parsed = shunfeng.parse_route_event(evt)
        # TODO: update shipments set status=..., temperature_log=append(...)
        logger.info("sf event=%s", parsed)
    return {"ok": True}
