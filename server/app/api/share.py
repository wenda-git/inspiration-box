from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_pool


router = APIRouter(prefix="/v1/share-reports", tags=["share-reports"])


class ShareReportCreate(BaseModel):
    source: str = Field(default="weekly", max_length=30)
    payload: dict[str, Any]


class ShareReportOut(BaseModel):
    share_id: str
    source: str
    payload: dict[str, Any]
    view_count: int
    created_at: datetime


def _parse_jsonb(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    return json.loads(v)


def _new_share_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]


@router.post("", response_model=ShareReportOut)
async def create_share_report(body: ShareReportCreate) -> ShareReportOut:
    pool = await get_pool()
    payload = json.dumps(body.payload, ensure_ascii=False)
    async with pool.acquire() as conn:
        row = None
        for _ in range(5):
            share_id = _new_share_id()
            row = await conn.fetchrow(
                """
                INSERT INTO share_reports (share_id, source, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (share_id) DO NOTHING
                RETURNING share_id, source, payload, view_count, created_at
                """,
                share_id, body.source, payload,
            )
            if row:
                break
        if not row:
            raise HTTPException(status_code=500, detail={"code": "SHARE_ID_FAILED", "message": "分享码生成失败"})

    return ShareReportOut(
        share_id=row["share_id"],
        source=row["source"],
        payload=_parse_jsonb(row["payload"]),
        view_count=row["view_count"],
        created_at=row["created_at"],
    )


@router.get("/{share_id}", response_model=ShareReportOut)
async def get_share_report(share_id: str) -> ShareReportOut:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE share_reports
               SET view_count = view_count + 1
             WHERE share_id = $1
               AND (expires_at IS NULL OR expires_at > now())
            RETURNING share_id, source, payload, view_count, created_at
            """,
            share_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "SHARE_NOT_FOUND", "message": "分享报告不存在或已过期"})
    return ShareReportOut(
        share_id=row["share_id"],
        source=row["source"],
        payload=_parse_jsonb(row["payload"]),
        view_count=row["view_count"],
        created_at=row["created_at"],
    )
