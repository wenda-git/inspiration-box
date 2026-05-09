"""
/v1/feedback · 用户对本周配单的反馈

反馈会影响下周配单：
  - rating <= 2 的水果，14 天内不会再被推荐（selector 里 recent_negative_feedback）
  - 破损需要带照片（MVP 不自动退款，人工介入）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import get_pool
from app.deps import current_user_id


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["feedback"])


VALID_KINDS = {"taste", "freshness", "damage", "allergy", "preference", "other"}


class FeedbackIn(BaseModel):
    fruit_code: str = Field(min_length=1, max_length=60)
    order_id: UUID | None = None
    kind: Literal["taste", "freshness", "damage", "allergy", "preference", "other"] = "taste"
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)
    photo_urls: list[str] = Field(default_factory=list, max_length=6)


class FeedbackOut(BaseModel):
    feedback_id: UUID
    will_affect_next_week: bool
    auto_refund_cny: float = 0.0
    learned_signals: list[dict] = Field(default_factory=list)


def _signals_from_feedback(body: FeedbackIn) -> list[tuple[str, str, float]]:
    """把一次反馈转成可累积的偏好权重。"""
    signals: list[tuple[str, str, float]] = []
    comment = (body.comment or "").strip()

    if body.rating is not None:
        if body.rating <= 2:
            signals.append(("fruit", body.fruit_code, -3.0))
        elif body.rating >= 4:
            signals.append(("fruit", body.fruit_code, 2.0))

    if body.kind == "allergy":
        signals.append(("fruit", body.fruit_code, -10.0))
    if body.kind == "damage":
        signals.append(("freshness", "sensitive", 2.0))
    if body.kind == "freshness":
        signals.append(("freshness", "sensitive", 1.0))

    rules = [
        (("下周别放", "别放", "不想吃"), ("fruit", body.fruit_code, -4.0)),
        (("喜欢", "保留", "相近口味"), ("fruit", body.fruit_code, 2.0)),
        (("太甜", "降低甜度"), ("taste", "sweet", -2.0)),
        (("太酸", "减少酸口"), ("taste", "sour", -2.0)),
        (("不新鲜", "新鲜度", "太生", "太硬", "熟度"), ("freshness", "sensitive", 2.0)),
        (("分量偏多", "分量多", "少放一点"), ("amount", f"less:{body.fruit_code}", 1.0)),
        (("分量偏少", "分量少", "多放一点"), ("amount", f"more:{body.fruit_code}", 1.0)),
    ]
    for needles, signal in rules:
        if any(n in comment for n in needles):
            signals.append(signal)

    # 合并同类信号
    merged: dict[tuple[str, str], float] = {}
    for typ, key, weight in signals:
        merged[(typ, key)] = merged.get((typ, key), 0.0) + weight
    return [(typ, key, weight) for (typ, key), weight in merged.items()]


async def _upsert_preference_signals(conn, user_id: UUID, feedback_id: UUID, signals: list[tuple[str, str, float]]) -> list[dict]:
    learned: list[dict] = []
    for signal_type, signal_key, delta in signals:
        row = await conn.fetchrow(
            """
            INSERT INTO user_preference_signals
                (user_id, signal_type, signal_key, weight, source_count,
                 last_feedback_id, last_seen_at, updated_at)
            VALUES ($1, $2, $3, $4, 1, $5, now(), now())
            ON CONFLICT (user_id, signal_type, signal_key)
            DO UPDATE SET
                weight = user_preference_signals.weight + EXCLUDED.weight,
                source_count = user_preference_signals.source_count + 1,
                last_feedback_id = EXCLUDED.last_feedback_id,
                last_seen_at = now(),
                updated_at = now()
            RETURNING signal_type, signal_key, weight, source_count
            """,
            user_id, signal_type, signal_key, delta, feedback_id,
        )
        learned.append({
            "signal_type": row["signal_type"],
            "signal_key": row["signal_key"],
            "weight": float(row["weight"]),
            "source_count": int(row["source_count"]),
        })
    return learned


@router.post("/feedback", response_model=FeedbackOut)
async def submit_feedback(
    body: FeedbackIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> FeedbackOut:
    # 破损必须带照片
    if body.kind == "damage" and not body.photo_urls:
        raise HTTPException(
            status_code=400,
            detail={"code": "PHOTOS_REQUIRED", "message": "破损反馈需要上传照片"},
        )

    user_id = UUID(user_id_str)
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()

    pool = await get_pool()
    learned_signals: list[dict] = []
    async with pool.acquire() as conn, conn.transaction():
        recommendation_id = None
        order_id = None

        if body.order_id is not None:
            order = await conn.fetchrow(
                """
                SELECT id, recommendation_id, items_snapshot, status
                  FROM orders
                 WHERE id = $1 AND user_id = $2
                 LIMIT 1
                """,
                body.order_id, user_id,
            )
            if order is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在"},
                )
            if order["status"] != "delivered":
                raise HTTPException(
                    status_code=400,
                    detail={"code": "ORDER_NOT_DELIVERED", "message": "签收后才能反馈这一箱"},
                )
            order_id = order["id"]
            recommendation_id = order["recommendation_id"]
            items = order["items_snapshot"]
            if isinstance(items, str):
                import json
                items = json.loads(items)
            allowed_codes = {it.get("fruit_code") for it in (items or [])}
            if body.fruit_code not in allowed_codes:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "FRUIT_NOT_IN_ORDER", "message": "这种水果不在这笔订单里"},
                )
        else:
            # 兼容当前周已签收场景：必须已有 delivered 订单，再校验 fruit_code 是否属于本周配单。
            current_order = await conn.fetchrow(
                """
                SELECT id, status
                  FROM orders
                 WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
                 LIMIT 1
                """,
                user_id, iso_year, iso_week,
            )
            if current_order is None or current_order["status"] != "delivered":
                raise HTTPException(
                    status_code=400,
                    detail={"code": "NO_FEEDBACK_TARGET", "message": "没有可反馈的已签收果箱"},
                )
            order_id = current_order["id"]
            weekly = await conn.fetchrow(
                """
                SELECT id, payload FROM weekly_recommendations
                 WHERE user_id = $1 AND iso_year = $2 AND iso_week = $3
                 LIMIT 1
                """,
                user_id, iso_year, iso_week,
            )
            if weekly is None:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "NO_FEEDBACK_TARGET", "message": "没有可反馈的已签收果箱"},
                )
            recommendation_id = weekly["id"]
            payload = weekly["payload"]
            if isinstance(payload, str):
                import json
                payload = json.loads(payload)
            items = (payload or {}).get("items", [])
            bonus = (payload or {}).get("bonus_items", [])
            allowed_codes = {it.get("fruit_code") for it in items} | {
                it.get("fruit_code") for it in bonus
            }
            if body.fruit_code not in allowed_codes:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "FRUIT_NOT_IN_WEEK",
                        "message": "这种水果不在你本周的配单里",
                    },
                )

        row = await conn.fetchrow(
            """
            INSERT INTO feedbacks
                (user_id, order_id, recommendation_id, fruit_id, kind, rating, comment, photo_urls)
            VALUES ($1, $2, $3, $4, $5::feedback_kind_t, $6, $7, $8)
            RETURNING id
            """,
            user_id,
            order_id,
            recommendation_id,
            body.fruit_code,
            body.kind,
            body.rating,
            body.comment,
            body.photo_urls,
        )
        learned_signals = await _upsert_preference_signals(
            conn,
            user_id,
            row["id"],
            _signals_from_feedback(body),
        )

    will_affect = (
        body.rating is not None
        or bool((body.comment or "").strip())
        or body.kind in {"preference", "allergy", "damage"}
        or bool(learned_signals)
    )
    return FeedbackOut(
        feedback_id=row["id"],
        will_affect_next_week=will_affect,
        auto_refund_cny=0.0,
        learned_signals=learned_signals,
    )


@router.get("/feedback/history")
async def feedback_history(
    user_id_str: Annotated[str, Depends(current_user_id)],
    limit: int = 20,
) -> list[dict]:
    """我的 · 反馈历史"""
    limit = max(1, min(limit, 50))
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.id, f.fruit_id, fr.name_cn AS fruit_name_cn, fr.emoji,
                   f.kind, f.rating, f.comment, f.created_at
              FROM feedbacks f
              LEFT JOIN fruits fr ON fr.code = f.fruit_id
             WHERE f.user_id = $1
             ORDER BY f.created_at DESC
             LIMIT $2
            """,
            user_id, limit,
        )
    return [
        {
            "id": str(r["id"]),
            "fruit_code": r["fruit_id"],
            "fruit_name_cn": r["fruit_name_cn"] or r["fruit_id"],
            "emoji": r["emoji"] or "🍎",
            "kind": r["kind"],
            "rating": r["rating"],
            "comment": r["comment"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/feedback/preferences")
async def feedback_preferences(
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> list[dict]:
    """当前用户被系统学到的偏好信号。"""
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT signal_type, signal_key, weight, source_count, last_seen_at
              FROM user_preference_signals
             WHERE user_id = $1
             ORDER BY abs(weight) DESC, last_seen_at DESC
             LIMIT 50
            """,
            user_id,
        )
    return [
        {
            "signal_type": r["signal_type"],
            "signal_key": r["signal_key"],
            "weight": float(r["weight"]),
            "source_count": int(r["source_count"]),
            "last_seen_at": r["last_seen_at"].isoformat(),
        }
        for r in rows
    ]
