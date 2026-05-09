from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import current_user_id
from app.services.recommend_orchestrator import (
    InventoryInsufficient,
    RegenLimitExceeded,
    current_week_recommendation,
    preview_recommendation,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["recommend"])


class PreviewProfile(BaseModel):
    gender: str = "other"
    height_cm: float | None = Field(default=None, ge=50, le=250)
    weight_kg: float | None = Field(default=None, ge=20, le=300)
    direction: str = "antiox"
    goals: list[str] = Field(default_factory=list, max_length=8)
    symptoms: list[str] = Field(default_factory=list, max_length=8)
    allergies: list[str] = Field(default_factory=list, max_length=20)
    dislikes: list[str] = Field(default_factory=list, max_length=20)
    prenatal_stage: str | None = None
    veg_freq: str | None = None
    sleep_pattern: str | None = None
    exercise_freq: str | None = None
    taste_prefer: list[str] = Field(default_factory=list, max_length=6)


@router.get("/recommend/current")
async def get_current_recommendation(
    user_id_str: Annotated[str, Depends(current_user_id)],
    refresh: bool = False,
) -> dict:
    try:
        return await current_week_recommendation(
            UUID(user_id_str), force_refresh=refresh,
        )
    except PermissionError as e:
        key = str(e)
        if key == "profile_required":
            raise HTTPException(
                status_code=422,
                detail={"code": "PROFILE_REQUIRED", "message": "请先完善健康画像"},
            )
        if key == "subscription_required":
            raise HTTPException(
                status_code=422,
                detail={"code": "SUBSCRIPTION_REQUIRED", "message": "请先选择健康方向"},
            )
        raise
    except RegenLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={"code": "REGEN_LIMIT", "message": str(e)},
        )
    except InventoryInsufficient as e:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVENTORY_INSUFFICIENT", "message": str(e)},
        )
    except Exception as e:
        logger.exception("recommend failed")
        raise HTTPException(
            status_code=502,
            detail={"code": "LLM_UPSTREAM_ERROR", "message": str(e)[:200]},
        )


@router.post("/recommend/preview")
async def get_preview_recommendation(body: PreviewProfile) -> dict:
    try:
        return await preview_recommendation(body.model_dump())
    except InventoryInsufficient as e:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVENTORY_INSUFFICIENT", "message": str(e)},
        )
    except Exception as e:
        logger.exception("preview recommend failed")
        raise HTTPException(
            status_code=502,
            detail={"code": "PREVIEW_FAILED", "message": str(e)[:200]},
        )
