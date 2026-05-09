"""
微信支付接入层 · 支持 mock / real 两种 provider。

对外提供 4 个能力：
  place_first_order    首单下单（支付 + 签约一次完成）
  charge_recurring     按已签合同扣一次钱（定时任务用）
  terminate_contract   终止合同
  verify_and_decrypt   回调验签 + 解密

生产接 APIv3 的地方都标了 TODO，等商户证书到位填上即可。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.config import get_settings


logger = logging.getLogger(__name__)


# ---------- 返回结构 ----------

@dataclass
class PaymentIntent:
    """给小程序 wx.requestPayment 用的支付参数 + 我方流水号"""
    provider: str                # "mock" | "real"
    payment_id: str              # 我方 payments.id
    out_trade_no: str            # 商户订单号 = idempotency_key
    prepay_id: str               # 小程序端 wx.requestPayment 的 package 参数
    amount_cny: float
    # mock 专用：直接告诉前端"假装已支付成功"
    auto_success: bool = False


@dataclass
class ContractResult:
    provider: str
    contract_id: str


# ---------- 公共接口 ----------

def is_mock() -> bool:
    return get_settings().wx_pay_provider != "real"


async def place_first_order(
    user_id: str,
    out_trade_no: str,
    amount_cny: float,
    description: str = "灵感果仓 首单订阅",
) -> PaymentIntent:
    """
    首单支付 + 签约一次完成。
    mock 模式：直接返回 auto_success=True，前端假装付成功
    real 模式：创建合同 + 获取 prepay_id
    """
    if is_mock():
        logger.info("💳 MOCK 首单下单: user=%s amount=¥%.2f trade=%s", user_id, amount_cny, out_trade_no)
        return PaymentIntent(
            provider="mock",
            payment_id="",
            out_trade_no=out_trade_no,
            prepay_id=f"mock_prepay_{uuid.uuid4().hex[:16]}",
            amount_cny=amount_cny,
            auto_success=True,
        )
    raise NotImplementedError("real 模式待接 APIv3")


async def charge_recurring(
    contract_id: str,
    out_trade_no: str,
    amount_cny: float,
    description: str = "灵感果仓 本周订阅",
) -> dict[str, Any]:
    """
    按合同扣款（周四定时任务用）。
    mock: 99% 成功，1% 失败用于测试 overdue 流程
    """
    if is_mock():
        import random
        fail_rate = get_settings().wx_pay_mock_fail_rate
        ok = random.random() >= fail_rate
        logger.info("💳 MOCK 续费扣款: contract=%s amount=¥%.2f → %s", contract_id, amount_cny, "OK" if ok else "FAIL")
        return {
            "ok": ok,
            "wx_tx_id": f"mock_tx_{uuid.uuid4().hex[:16]}" if ok else None,
            "error": None if ok else "card_balance_not_enough",
        }
    raise NotImplementedError("real 模式待接 APIv3")


async def terminate_contract(contract_id: str) -> None:
    """取消订阅合同"""
    if is_mock():
        logger.info("💳 MOCK 终止合同: %s", contract_id)
        return
    raise NotImplementedError("real 模式待接 APIv3")


async def verify_and_decrypt_notify(headers: dict, raw_body: bytes) -> dict[str, Any] | None:
    """回调验签 + 解密。mock 模式没有回调。"""
    if is_mock():
        return None
    raise NotImplementedError("real 模式待接 APIv3")


# ---------- 合同号生成 ----------

def new_contract_id() -> str:
    """mock 合同号"""
    return f"mock_contract_{uuid.uuid4().hex[:24]}"


def new_out_trade_no(prefix: str = "VL") -> str:
    """我方订单号，≤ 32 字符，在 payments.idempotency_key 里唯一"""
    return f"{prefix}{int(time.time())}{uuid.uuid4().hex[:10]}"
