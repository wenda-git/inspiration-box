"""
集中管理运行期配置。生产用环境变量注入，不提交真实密钥。
本地开发自动从 server/.env 加载。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

# 自动读 server/.env（已存在的系统环境变量优先，不会被覆盖）
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


class Settings(BaseModel):
    # 微信小程序支付（直连商户）
    wx_appid: str = os.getenv("WX_APPID", "")
    wx_mchid: str = os.getenv("WX_MCHID", "")
    wx_api_v3_key: str = os.getenv("WX_API_V3_KEY", "")         # APIv3 密钥（对称）
    wx_serial_no: str = os.getenv("WX_SERIAL_NO", "")            # 商户证书序列号
    wx_private_key_path: str = os.getenv("WX_PRIVATE_KEY_PATH", "") # 商户 apiclient_key.pem
    wx_notify_url: str = os.getenv(
        "WX_NOTIFY_URL", "https://api.inspirationbox.app/v1/wxpay/notify"
    )
    # 订阅扣费商品 ID（微信后台配置）
    wx_product_id_weekly: str = os.getenv("WX_PRODUCT_ID_WEEKLY", "inspirationbox_weekly")

    # 顺丰冷链（示例：顺丰开放平台 BSP 接口）
    sf_partner_id: str = os.getenv("SF_PARTNER_ID", "")
    sf_request_id: str = os.getenv("SF_REQUEST_ID", "")
    sf_secret: str = os.getenv("SF_SECRET", "")
    sf_api_base: str = os.getenv("SF_API_BASE", "https://bspgw.sf-express.com/std/service")
    sf_monthly_card: str = os.getenv("SF_MONTHLY_CARD", "")       # 月结卡号

    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    jwt_ttl_days: int = int(os.getenv("JWT_TTL_DAYS", "30"))

    # OpenAI 配单模型
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    # 短信服务（阿里云 dysmsapi 或腾讯云 sms，先 mock）
    sms_provider: str = os.getenv("SMS_PROVIDER", "mock")   # mock | aliyun | aliyun_pnvs | tencent
    sms_access_key: str = os.getenv("SMS_ACCESS_KEY") or os.getenv("ALIYUN_ACCESS_KEY_ID", "")
    sms_access_secret: str = os.getenv("SMS_ACCESS_SECRET") or os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
    sms_sign_name: str = os.getenv("SMS_SIGN_NAME") or os.getenv("ALIYUN_SMS_SIGN_NAME", "")
    sms_template_id: str = os.getenv("SMS_TEMPLATE_ID") or os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")
    sms_region_id: str = os.getenv("SMS_REGION_ID") or os.getenv("ALIYUN_SMS_REGION_ID", "cn-hangzhou")
    sms_endpoint: str = os.getenv("SMS_ENDPOINT") or os.getenv("ALIYUN_SMS_ENDPOINT", "dysmsapi.aliyuncs.com")
    sms_scheme_name: str = os.getenv("SMS_SCHEME_NAME", "")
    sms_valid_time: int = int(os.getenv("SMS_VALID_TIME", "300"))
    sms_code_salt: str = os.getenv("SMS_CODE_SALT", "dev-salt-change-me")

    # 私有 OSS 图片资源。小程序访问 /v1/assets/...，后端签名后跳转到 OSS。
    oss_bucket: str = os.getenv("OSS_BUCKET", "inspiration-images")
    oss_endpoint: str = os.getenv("OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com")
    oss_asset_prefix: str = os.getenv("OSS_ASSET_PREFIX", "miniprogram/assets")
    oss_access_key: str = os.getenv("OSS_ACCESS_KEY") or os.getenv("OSS_ACCESS_KEY_ID") or sms_access_key
    oss_access_secret: str = os.getenv("OSS_ACCESS_SECRET") or os.getenv("OSS_ACCESS_KEY_SECRET") or sms_access_secret
    oss_signed_url_ttl: int = int(os.getenv("OSS_SIGNED_URL_TTL", "3600"))

    # 后台
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin")
    admin_session_secret: str = os.getenv("ADMIN_SESSION_SECRET", "dev-session-secret")

    # 微信支付 provider
    # mock: 返回假 prepay_id, 小程序 wx.requestPayment 直接 success, 方便开发 demo
    # real: 调真 APIv3, 需要上面 wx_* 配置齐全
    wx_pay_provider: str = os.getenv("WX_PAY_PROVIDER", "mock")
    # mock 下是否随机失败（用于测试 overdue 流程），默认不失败
    wx_pay_mock_fail_rate: float = float(os.getenv("WX_PAY_MOCK_FAIL_RATE", "0"))

    # 首单折扣 + 后续价
    plan_first_payment_cny: float = float(os.getenv("PLAN_FIRST_PAYMENT_CNY", "99"))

    # 环境标记
    env: str = os.getenv("APP_ENV", "dev")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # 生产环境禁止使用默认 secret
    if s.env in ("prod", "production"):
        dangerous = {
            "jwt_secret": s.jwt_secret,
            "sms_code_salt": s.sms_code_salt,
            "admin_session_secret": s.admin_session_secret,
            "admin_password": s.admin_password,
        }
        bad = [k for k, v in dangerous.items() if not v or "CHANGE_ME" in v or "dev-" in v or v == "admin"]
        if bad:
            raise RuntimeError(
                f"生产环境检测到默认/示例 secret，请先设置环境变量：{', '.join(bad)}"
            )
    return s
