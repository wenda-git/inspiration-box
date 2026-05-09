"""
顺丰冷链 BSP 对接 · 下单 + 轨迹回传

文档：顺丰开放平台 → 冷链专送（SF-Freeze）
接口示例：
  EXP_RECE_CREATE_ORDER         下单
  EXP_RECE_SEARCH_ROUTES        查询路由（轨迹）
  EXP_RECE_SUBSCRIBE_ROUTES     订阅轨迹推送（建议走推送，省轮询）

安全：
  BSP 用 MD5(msgData + timestamp + checkword) 签名；生产上要把 checkword 放 KMS，
  不要写死在 env。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


@dataclass
class ShipmentDraft:
    order_id: str                 # 我方 orders.id
    receiver_name: str
    receiver_phone: str
    receiver_address: str         # 省市区详细地址
    sender_name: str = "灵感果仓 云仓"
    sender_phone: str = "4000000000"
    sender_address: str = "广东省广州市白云区云仓 A12"
    total_weight_kg: float = 3.0   # 约 2.5kg 水果 + 冰袋


@dataclass
class ShipmentResult:
    carrier_code: str = "sf_cold"
    tracking_no: str = ""
    raw: dict[str, Any] = None


def _sign(msg_data: str, timestamp: str, checkword: str) -> str:
    raw = msg_data + timestamp + checkword
    md5 = hashlib.md5(raw.encode()).digest()
    import base64
    return base64.b64encode(md5).decode()


async def _call(service: str, msg_data: dict) -> dict:
    settings = get_settings()
    timestamp = str(int(time.time() * 1000))
    msg_str = json.dumps(msg_data, separators=(",", ":"), ensure_ascii=False)
    form = {
        "partnerID": settings.sf_partner_id,
        "requestID": settings.sf_request_id,
        "serviceCode": service,
        "timestamp": timestamp,
        "msgDigest": _sign(msg_str, timestamp, settings.sf_secret),
        "msgData": msg_str,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            settings.sf_api_base,
            content=urllib.parse.urlencode(form),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


async def create_shipment(draft: ShipmentDraft) -> ShipmentResult:
    settings = get_settings()
    msg = {
        "orderId": draft.order_id,
        "expressTypeId": 12,                      # 12 = 冷链专送
        "payMethod": 1,                           # 1 = 寄方月结
        "customsBatchs": 1,
        "parcelQty": 1,
        "totalWeight": draft.total_weight_kg,
        "monthlyCard": settings.sf_monthly_card,
        "cargoDetails": [{"name": "水果", "count": 1, "unit": "箱"}],
        "contactInfoList": [
            {
                "contactType": 1,                 # 1 = 寄件方
                "company": draft.sender_name,
                "contact": draft.sender_name,
                "tel": draft.sender_phone,
                "address": draft.sender_address,
            },
            {
                "contactType": 2,                 # 2 = 收件方
                "contact": draft.receiver_name,
                "tel": draft.receiver_phone,
                "address": draft.receiver_address,
            },
        ],
    }
    data = await _call("EXP_RECE_CREATE_ORDER", msg)
    body = data.get("apiResultData", {})
    return ShipmentResult(
        tracking_no=body.get("mailNo", ""),
        raw=data,
    )


async def search_routes(tracking_no: str) -> list[dict]:
    """主动查询（建议改订阅推送节省额度）。"""
    msg = {"trackingType": 1, "trackingNumber": [tracking_no], "methodType": 1}
    data = await _call("EXP_RECE_SEARCH_ROUTES", msg)
    return data.get("apiResultData", {}).get("msgData", {}).get("routeResps", [])


def parse_route_event(evt: dict) -> dict:
    """
    把顺丰的 opCode 规范化成我们 shipments.status 枚举。
    opCode 参考表（精简）：
      50=揽收  54=已发出  70=派送中  80=已签收  99=异常
    """
    code = str(evt.get("opCode", ""))
    mapping = {
        "50": "picked",
        "54": "in_transit",
        "70": "out_for_delivery",
        "80": "delivered",
        "99": "exception",
    }
    return {
        "status": mapping.get(code, "in_transit"),
        "at": evt.get("acceptTime"),
        "location": evt.get("acceptAddress", ""),
        "remark": evt.get("remark", ""),
    }
