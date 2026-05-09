"""
短信服务抽象。mock 打印到 stdout，生产切换到阿里云 dysmsapi 或腾讯云 SMS。

限流约束（provider 之上）：
  - 同一手机号 60s 内只能发 1 条
  - 同一手机号 24h 内最多 5 条
  这层限流在 auth 服务里做，provider 只负责发送。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class SmsProvider(ABC):
    @abstractmethod
    async def send_code(self, phone_e164: str, code: str) -> None: ...


class MockSmsProvider(SmsProvider):
    async def send_code(self, phone_e164: str, code: str) -> None:
        logger.warning("📱 MOCK SMS → %s: 您的 灵感果仓 验证码是 %s（5 分钟有效）", phone_e164, code)


class AliyunRpcMixin:
    def _phone_for_aliyun(self, phone_e164: str) -> str:
        phone = phone_e164.strip().replace(" ", "").replace("-", "")
        if phone.startswith("+86") and len(phone) == 14:
            return phone[3:]
        if phone.startswith("+"):
            return phone[1:]
        return phone

    def _percent_encode(self, value: str) -> str:
        return quote(str(value), safe="~")

    def _signature(self, params: dict[str, str], access_secret: str) -> str:
        canonical = "&".join(
            f"{self._percent_encode(k)}={self._percent_encode(params[k])}"
            for k in sorted(params)
        )
        string_to_sign = f"GET&%2F&{self._percent_encode(canonical)}"
        key = f"{access_secret}&".encode("utf-8")
        digest = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _required_settings(self) -> tuple:
        settings = get_settings()
        missing = [
            name for name, value in {
                "SMS_ACCESS_KEY / ALIYUN_ACCESS_KEY_ID": settings.sms_access_key,
                "SMS_ACCESS_SECRET / ALIYUN_ACCESS_KEY_SECRET": settings.sms_access_secret,
                "SMS_SIGN_NAME / ALIYUN_SMS_SIGN_NAME": settings.sms_sign_name,
                "SMS_TEMPLATE_ID / ALIYUN_SMS_TEMPLATE_CODE": settings.sms_template_id,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"阿里云短信配置缺失: {', '.join(missing)}")
        return settings, missing

    def _common_rpc_params(self, action: str, version: str) -> dict[str, str]:
        settings = get_settings()
        return {
            "AccessKeyId": settings.sms_access_key,
            "Action": action,
            "Format": "JSON",
            "RegionId": settings.sms_region_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": version,
        }

    async def _get(self, endpoint: str, params: dict[str, str]) -> dict:
        settings = get_settings()
        params["Signature"] = self._signature(params, settings.sms_access_secret)
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"https://{endpoint}/", params=params)
        resp.raise_for_status()
        return resp.json()


class AliyunSmsProvider(AliyunRpcMixin, SmsProvider):
    """
    阿里云短信服务 SendSms RPC 接口。
    模板示例：您的验证码是 ${code}，5 分钟内有效。
    """

    async def send_code(self, phone_e164: str, code: str) -> None:
        settings, _ = self._required_settings()
        params = self._common_rpc_params("SendSms", "2017-05-25") | {
            "Action": "SendSms",
            "PhoneNumbers": self._phone_for_aliyun(phone_e164),
            "SignName": settings.sms_sign_name,
            "TemplateCode": settings.sms_template_id,
            "TemplateParam": json.dumps({"code": code}, separators=(",", ":"), ensure_ascii=False),
        }
        data = await self._get(settings.sms_endpoint, params)
        if data.get("Code") != "OK":
            logger.warning("Aliyun SMS failed: phone=%s code=%s message=%s", phone_e164, data.get("Code"), data.get("Message"))
            raise RuntimeError(f"阿里云短信发送失败: {data.get('Code') or 'UNKNOWN'}")
        logger.info("Aliyun SMS sent: phone=%s request_id=%s", phone_e164, data.get("RequestId"))


class AliyunPnvsSmsProvider(AliyunRpcMixin, SmsProvider):
    """
    阿里云号码认证服务 PNVS · SendSmsVerifyCode。

    这里仍然传入本系统生成的验证码，并由本系统本地校验，避免改动登录流程。
    如果后续希望完全使用阿里云生成和核验验证码，可再接 CheckSmsVerifyCode。
    """

    async def send_code(self, phone_e164: str, code: str) -> None:
        settings, _ = self._required_settings()
        endpoint = (
            settings.sms_endpoint
            if settings.sms_endpoint and settings.sms_endpoint != "dysmsapi.aliyuncs.com"
            else "dypnsapi.aliyuncs.com"
        )
        params = self._common_rpc_params("SendSmsVerifyCode", "2017-05-25") | {
            "PhoneNumber": self._phone_for_aliyun(phone_e164),
            "CountryCode": "86",
            "SignName": settings.sms_sign_name,
            "TemplateCode": settings.sms_template_id,
            "TemplateParam": json.dumps(
                {"code": code, "min": str(max(1, settings.sms_valid_time // 60))},
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            "CodeLength": str(len(code)),
            "ValidTime": str(settings.sms_valid_time),
            "DuplicatePolicy": "1",
            "Interval": "60",
            "CodeType": "1",
            "ReturnVerifyCode": "false",
            "AutoRetry": "1",
        }
        if settings.sms_scheme_name:
            params["SchemeName"] = settings.sms_scheme_name

        data = await self._get(endpoint, params)
        success = data.get("Code") == "OK" and data.get("Success", True) is not False
        if not success:
            logger.warning(
                "Aliyun PNVS SMS failed: phone=%s code=%s message=%s",
                phone_e164, data.get("Code"), data.get("Message"),
            )
            raise RuntimeError(f"阿里云号码认证短信发送失败: {data.get('Code') or 'UNKNOWN'}")
        logger.info("Aliyun PNVS SMS sent: phone=%s request_id=%s", phone_e164, data.get("RequestId"))


class TencentSmsProvider(SmsProvider):
    async def send_code(self, phone_e164: str, code: str) -> None:
        # TODO: tencentcloud.sms.v20210111.sms_client
        raise NotImplementedError


def get_sms_provider() -> SmsProvider:
    provider = get_settings().sms_provider
    if provider in ("aliyun", "aliyun_dysms"):
        return AliyunSmsProvider()
    if provider in ("aliyun_pnvs", "pnvs"):
        return AliyunPnvsSmsProvider()
    if provider == "tencent":
        return TencentSmsProvider()
    return MockSmsProvider()
