"""
FastAPI 依赖注入：Authorization: Bearer <jwt> → 当前 user_id
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException

from app.db import get_pool
from app.services.auth import decode_jwt


async def current_user_id(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_INVALID", "message": "缺少或非法的 Authorization header"},
        )
    token = authorization[7:]
    try:
        payload = decode_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_EXPIRED", "message": "登录已过期，请重新登录"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_INVALID", "message": "登录凭证无效"},
        )
    user_id = payload.get("sub")
    try:
        user_uuid = UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_INVALID", "message": "登录凭证无效"},
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1 AND deleted_at IS NULL)",
            user_uuid,
        )
    if not exists:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_USER_MISSING", "message": "登录状态已失效，请重新登录"},
        )
    return str(user_uuid)
