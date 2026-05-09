"""
/v1/uploads/* · 小程序端临时素材上传。

当前只开放反馈照片上传：后端落盘到 server/uploads/feedback，
并通过 /uploads/feedback/... 静态路径提供给后台人工查看。
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.deps import current_user_id


router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads"
FEEDBACK_DIR = UPLOAD_ROOT / "feedback"
MAX_FEEDBACK_PHOTO_BYTES = 4 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _detect_image_ext(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("/feedback-photo")
async def upload_feedback_photo(
    user_id_str: Annotated[str, Depends(current_user_id)],
    file: UploadFile = File(...),
) -> dict:
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNSUPPORTED_FILE", "message": "只支持 JPG、PNG 或 WebP 图片"},
        )

    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=400,
            detail={"code": "EMPTY_FILE", "message": "图片为空"},
        )
    if len(data) > MAX_FEEDBACK_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "FILE_TOO_LARGE", "message": "单张照片不能超过 4MB"},
        )

    ext = _detect_image_ext(data)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_IMAGE", "message": "图片格式无法识别"},
        )

    user_id = UUID(user_id_str)
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id.hex[:12]}-{secrets.token_hex(12)}.{ext}"
    path = FEEDBACK_DIR / filename
    path.write_bytes(data)

    return {
        "url": f"/uploads/feedback/{filename}",
        "size": len(data),
        "content_type": content_type,
    }
