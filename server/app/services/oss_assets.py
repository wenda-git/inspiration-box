from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote

from app.config import get_settings


def _safe_key(path: str) -> str:
    cleaned = path.strip().lstrip("/")
    parts = [p for p in cleaned.split("/") if p and p not in (".", "..")]
    if not parts:
        raise ValueError("empty asset path")
    return "/".join(parts)


def oss_object_key(path: str) -> str:
    settings = get_settings()
    prefix = (settings.oss_asset_prefix or "").strip("/")
    key = _safe_key(path)
    return f"{prefix}/{key}" if prefix else key


def signed_get_url(path: str, *, expires_in: int | None = None) -> str:
    settings = get_settings()
    if not settings.oss_access_key or not settings.oss_access_secret:
        raise RuntimeError("OSS access key is not configured")
    bucket = settings.oss_bucket
    endpoint = settings.oss_endpoint
    key = oss_object_key(path)
    expires = int(time.time()) + int(expires_in or settings.oss_signed_url_ttl)
    resource = f"/{bucket}/{key}"
    string_to_sign = f"GET\n\n\n{expires}\n{resource}"
    digest = hmac.new(
        settings.oss_access_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = quote(base64.b64encode(digest).decode("utf-8"), safe="")
    encoded_key = quote(key, safe="/")
    return (
        f"https://{bucket}.{endpoint}/{encoded_key}"
        f"?OSSAccessKeyId={quote(settings.oss_access_key, safe='')}"
        f"&Expires={expires}"
        f"&Signature={signature}"
    )
