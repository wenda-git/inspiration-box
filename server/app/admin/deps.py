"""
后台登录守卫。signed cookie (itsdangerous)，无需第三方 session 库。
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings


COOKIE_NAME = "admin_session"


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().admin_session_secret, salt="admin")


def make_session_cookie(username: str) -> str:
    return _signer().dumps({"u": username})


def read_session(token: str | None) -> str | None:
    if not token:
        return None
    try:
        payload = _signer().loads(token)
        return payload.get("u")
    except BadSignature:
        return None


class NotLoggedIn(Exception):
    pass


def require_admin(request: Request) -> str:
    """
    未登录 → 抛 NotLoggedIn，全局异常处理器转 302 到 /admin/login
    """
    token = request.cookies.get(COOKIE_NAME)
    user = read_session(token)
    if user is None:
        raise NotLoggedIn()
    request.state.admin_user = user
    return user


def require_admin_json(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    user = read_session(token)
    if user is None:
        raise HTTPException(status_code=401, detail="login_required")
    request.state.admin_user = user
    return user
