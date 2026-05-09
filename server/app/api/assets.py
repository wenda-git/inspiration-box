from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.services.oss_assets import signed_get_url

router = APIRouter(prefix="/v1/assets", tags=["assets"])


@router.get("/{path:path}")
async def get_asset(path: str) -> RedirectResponse:
    try:
        url = signed_get_url(path)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "ASSET_NOT_FOUND"})
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail={"code": "OSS_NOT_CONFIGURED", "message": "图片服务暂未配置"},
        )
    return RedirectResponse(url, status_code=302)
