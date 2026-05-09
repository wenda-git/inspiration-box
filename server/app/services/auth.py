"""
手机号 + 短信验证码登录 + JWT 签发。

流程：
  POST /v1/auth/request-code  { phone } → 200 { cooldown_sec: 60 }
  POST /v1/auth/verify-code   { phone, code } → 200 { token, user }

安全要点：
  * code 只存 sha256(code + salt)，不存明文
  * 60s 重发冷却 + 24h 单号发送上限 5 条 + 单 code 校验尝试 5 次失败作废
  * JWT 30 天 TTL，退出登录客户端删 token 即可（无黑名单表）
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt

from app.config import get_settings
from app.db import get_pool
from app.integrations.sms import get_sms_provider


import os
logger = logging.getLogger(__name__)
CODE_TTL_MINUTES = 5
COOLDOWN_SEC = int(os.getenv("SMS_COOLDOWN_SEC", "60"))
DAILY_CAP = int(os.getenv("SMS_DAILY_CAP", "5"))
VERIFY_MAX_ATTEMPTS = 5


# ---------- 异常 ----------

class AuthError(Exception):
    code: str = "AUTH_ERROR"
    http: int = 400

class CooldownActive(AuthError):
    code = "SMS_COOLDOWN"; http = 429
    def __init__(self, wait_sec: int) -> None:
        super().__init__(f"请 {wait_sec}s 后再试")
        self.wait_sec = wait_sec

class DailyCapExceeded(AuthError):
    code = "SMS_DAILY_CAP"; http = 429

class CodeInvalid(AuthError):
    code = "CODE_INVALID"; http = 400

class CodeExpired(AuthError):
    code = "CODE_EXPIRED"; http = 400

class SmsDeliveryFailed(AuthError):
    code = "SMS_DELIVERY_FAILED"; http = 503


# ---------- 工具 ----------

def _hash_code(code: str) -> str:
    salt = get_settings().sms_code_salt
    return hashlib.sha256(f"{code}|{salt}".encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _normalize_phone(raw: str) -> str:
    """把用户输入归一成 E.164。MVP 只做国内号。"""
    s = raw.strip().replace(" ", "").replace("-", "")
    if s.startswith("+"):
        return s
    if len(s) == 11 and s.startswith("1"):
        return f"+86{s}"
    raise AuthError(f"非法手机号: {raw}")


# ---------- 对外服务 ----------

@dataclass
class User:
    id: UUID
    phone_e164: str
    nickname: str | None = None
    avatar_url: str | None = None


async def request_code(phone_raw: str) -> dict[str, Any]:
    phone = _normalize_phone(phone_raw)
    pool = await get_pool()

    async with pool.acquire() as conn:
        # 60s 冷却
        latest = await conn.fetchrow(
            """
            SELECT created_at FROM sms_codes
             WHERE phone_e164 = $1
             ORDER BY created_at DESC
             LIMIT 1
            """,
            phone,
        )
        if latest:
            elapsed = (datetime.now(timezone.utc) - latest["created_at"]).total_seconds()
            if elapsed < COOLDOWN_SEC:
                raise CooldownActive(wait_sec=int(COOLDOWN_SEC - elapsed))

        # 24h 单号上限 5 条
        sent_24h = await conn.fetchval(
            """
            SELECT count(*) FROM sms_codes
             WHERE phone_e164 = $1
               AND created_at > now() - interval '24 hours'
            """,
            phone,
        )
        if sent_24h >= DAILY_CAP:
            raise DailyCapExceeded("今日发送次数已达上限")

        code = _generate_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)
        sms_id = await conn.fetchval(
            """
            INSERT INTO sms_codes (phone_e164, code_hash, purpose, expires_at)
            VALUES ($1, $2, 'login', $3)
            RETURNING id
            """,
            phone, _hash_code(code), expires_at,
        )

    try:
        await get_sms_provider().send_code(phone, code)
    except Exception as e:
        logger.exception("sms delivery failed: phone=%s", phone)
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM sms_codes WHERE id = $1", sms_id)
        raise SmsDeliveryFailed("验证码发送失败，请稍后再试") from e
    return {"cooldown_sec": COOLDOWN_SEC, "expires_at": expires_at.isoformat()}


async def verify_code(phone_raw: str, code: str) -> dict[str, Any]:
    phone = _normalize_phone(phone_raw)
    code_hash = _hash_code(code)
    pool = await get_pool()

    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT id, code_hash, attempts, expires_at
              FROM sms_codes
             WHERE phone_e164 = $1 AND consumed_at IS NULL
             ORDER BY created_at DESC
             LIMIT 1
            FOR UPDATE
            """,
            phone,
        )
        if row is None:
            raise CodeInvalid("请先获取验证码")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise CodeExpired("验证码已过期")
        if row["attempts"] >= VERIFY_MAX_ATTEMPTS:
            raise CodeInvalid("尝试次数过多，请重新获取")

        if row["code_hash"] != code_hash:
            await conn.execute(
                "UPDATE sms_codes SET attempts = attempts + 1 WHERE id = $1",
                row["id"],
            )
            raise CodeInvalid("验证码错误")

        await conn.execute(
            "UPDATE sms_codes SET consumed_at = now() WHERE id = $1",
            row["id"],
        )

        # 首次登录自动建用户；已有则取出
        user_row = await conn.fetchrow(
            """
            SELECT id, phone_e164, nickname, avatar_url
              FROM users
             WHERE phone_e164 = $1 AND deleted_at IS NULL
            """,
            phone,
        )
        if user_row is None:
            user_row = await conn.fetchrow(
                """
                INSERT INTO users (phone_e164)
                     VALUES ($1)
                  RETURNING id, phone_e164, nickname, avatar_url
                """,
                phone,
            )

    user = User(
        id=user_row["id"],
        phone_e164=user_row["phone_e164"],
        nickname=user_row["nickname"],
        avatar_url=user_row["avatar_url"],
    )
    token = issue_jwt(str(user.id))
    return {
        "token": token,
        "user": {
            "id": str(user.id),
            "phone_e164": user.phone_e164,
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
        },
    }


# ---------- JWT ----------

def issue_jwt(user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
