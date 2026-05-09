#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import mimetypes
import os
import ssl
import sys
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "server" / ".env"
ASSET_ROOT = ROOT / "miniprogram" / "assets"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        out.setdefault(key, value)
    return out


def content_type(path: Path) -> str:
    if path.suffix.lower() == ".svg":
        return "image/svg+xml"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def sign(method: str, bucket: str, key: str, content_type_: str, date: str, secret: str) -> str:
    canonical = f"{method}\n\n{content_type_}\n{date}\n/{bucket}/{key}"
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def iter_assets() -> list[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    files: list[Path] = []
    for path in ASSET_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        rel = path.relative_to(ASSET_ROOT)
        if rel.parts and rel.parts[0] == "tabbar":
            continue
        files.append(path)
    return sorted(files)


def upload_file(path: Path, *, bucket: str, endpoint: str, prefix: str, access_key: str, secret: str) -> None:
    rel = path.relative_to(ASSET_ROOT).as_posix()
    key = f"{prefix.strip('/')}/{rel}" if prefix.strip("/") else rel
    ctype = content_type(path)
    date = formatdate(usegmt=True)
    signature = sign("PUT", bucket, key, ctype, date, secret)
    url = f"https://{bucket}.{endpoint}/{quote(key, safe='/')}"
    data = path.read_bytes()
    req = Request(url, data=data, method="PUT")
    req.add_header("Date", date)
    req.add_header("Content-Type", ctype)
    req.add_header("Content-Length", str(len(data)))
    req.add_header("Authorization", f"OSS {access_key}:{signature}")
    with urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"upload failed: {path} -> {resp.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload miniprogram image assets to Aliyun OSS.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env = load_env()
    bucket = env.get("OSS_BUCKET", "inspiration-images")
    endpoint = env.get("OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com")
    prefix = env.get("OSS_ASSET_PREFIX", "miniprogram/assets")
    access_key = env.get("OSS_ACCESS_KEY") or env.get("OSS_ACCESS_KEY_ID") or env.get("SMS_ACCESS_KEY")
    secret = env.get("OSS_ACCESS_SECRET") or env.get("OSS_ACCESS_KEY_SECRET") or env.get("SMS_ACCESS_SECRET")

    if not access_key or not secret:
        print("missing OSS_ACCESS_KEY/OSS_ACCESS_SECRET (or SMS_ACCESS_KEY/SMS_ACCESS_SECRET fallback)", file=sys.stderr)
        return 2

    files = iter_assets()
    print(f"bucket={bucket} endpoint={endpoint} prefix={prefix}")
    print(f"uploading {len(files)} assets; tabbar assets stay local")
    for path in files:
        rel = path.relative_to(ASSET_ROOT).as_posix()
        if args.dry_run:
            print(f"DRY {rel}")
        else:
            upload_file(path, bucket=bucket, endpoint=endpoint, prefix=prefix, access_key=access_key, secret=secret)
            print(f"OK  {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
