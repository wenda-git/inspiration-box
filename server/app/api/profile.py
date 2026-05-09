"""
健康画像 · 当前用户的 is_current 那一行。

每次 POST 采用"快照式"：把旧画像 is_current=false，插入新一行。
好处：有历史可回溯；坏处：量大需清理（90 天自动回收）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.db import get_pool
from app.deps import current_user_id


router = APIRouter(prefix="/v1/profile", tags=["profile"])


GOAL_CHOICES = {
    # 主方向（和 subscription.plan_code 对应）
    "weight_loss", "glucose_control", "prenatal",
    "anti_aging", "sports_recovery", "immune_low", "general_wellness",
    "immune", "gut_health",
    # 症状标签（软性诉求）
    "fatigue", "insomnia", "hair_loss", "constipation", "dull_skin",
    "high_glucose", "high_pressure", "high_uric", "easy_cold",
    "period_discomfort",
}


PRENATAL_STAGES = {None, "", "trying", "first_tri", "second_tri", "third_tri", "postpartum"}
VEG_FREQ = {None, "", "almost_none", "light", "moderate", "plenty"}
SLEEP_PATTERN = {None, "", "regular", "late", "very_late", "shift"}
EXERCISE_FREQ = {None, "", "none", "light", "moderate", "heavy"}
TASTE_CHOICES = {"sweet", "sour", "crisp", "juicy", "aroma", "mild"}


class ProfileIn(BaseModel):
    gender: str = Field(pattern="^(male|female|other)$")
    height_cm: float = Field(ge=50, le=250)
    weight_kg: float = Field(ge=20, le=300)
    goals: list[str] = Field(default_factory=list, max_length=8)
    allergies: list[str] = Field(default_factory=list, max_length=20)
    dislikes: list[str] = Field(default_factory=list, max_length=20)
    prenatal_stage: str | None = None
    # 新增：生活习惯信号
    veg_freq: str | None = None
    sleep_pattern: str | None = None
    exercise_freq: str | None = None
    taste_prefer: list[str] = Field(default_factory=list, max_length=6)


class Profile(BaseModel):
    id: UUID
    gender: str
    height_cm: float
    weight_kg: float
    bmi: float
    bmi_band: str | None = None
    goals: list[str] = []
    allergies: list[str] = []
    dislikes: list[str] = []
    prenatal_stage: str | None = None
    veg_freq: str | None = None
    sleep_pattern: str | None = None
    exercise_freq: str | None = None
    taste_prefer: list[str] = []
    created_at: datetime


def _calc_bmi(h_cm: float, w_kg: float) -> float:
    h_m = h_cm / 100.0
    return round(w_kg / (h_m * h_m), 2)


def _bmi_band(bmi: float) -> str:
    if bmi < 18.5: return "underweight"
    if bmi < 24.0: return "normal"
    if bmi < 28.0: return "overweight"
    return "obese"


def _normalize_list(raw: list[str]) -> list[str]:
    """把前端传进来的 '苹果, 香蕉' 这种逗号串拆成数组，过滤空项。"""
    out: list[str] = []
    for item in raw:
        for part in (item or "").replace("，", ",").split(","):
            s = part.strip()
            if s and s not in out:
                out.append(s)
    return out


SELECT_COLS = """
    id, gender, height_cm, weight_kg, bmi, bmi_band,
    goals, allergies, dislikes, prenatal_stage,
    veg_freq, sleep_pattern, exercise_freq, taste_prefer,
    created_at
"""


def _row_to_profile(row) -> Profile:
    return Profile(
        id=row["id"],
        gender=row["gender"],
        height_cm=float(row["height_cm"]),
        weight_kg=float(row["weight_kg"]),
        bmi=float(row["bmi"]),
        bmi_band=row["bmi_band"],
        goals=list(row["goals"] or []),
        allergies=list(row["allergies"] or []),
        dislikes=list(row["dislikes"] or []),
        prenatal_stage=row["prenatal_stage"],
        veg_freq=row["veg_freq"],
        sleep_pattern=row["sleep_pattern"],
        exercise_freq=row["exercise_freq"],
        taste_prefer=list(row["taste_prefer"] or []),
        created_at=row["created_at"],
    )


@router.get("", response_model=Profile | None)
async def get_profile(
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Profile | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {SELECT_COLS} FROM health_profiles "
            "WHERE user_id = $1 AND is_current = TRUE LIMIT 1",
            UUID(user_id_str),
        )
    return _row_to_profile(row) if row else None


@router.post("", response_model=Profile, status_code=status.HTTP_201_CREATED)
async def save_profile(
    body: ProfileIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Profile:
    bad_goals = set(body.goals) - GOAL_CHOICES
    if bad_goals:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_GOALS", "message": f"未知目标: {bad_goals}"},
        )

    user_id = UUID(user_id_str)
    bmi = _calc_bmi(body.height_cm, body.weight_kg)
    allergies = _normalize_list(body.allergies)
    dislikes = _normalize_list(body.dislikes)
    # 过滤无效 taste
    taste_prefer = [t for t in (body.taste_prefer or []) if t in TASTE_CHOICES]

    stage = body.prenatal_stage if body.prenatal_stage in PRENATAL_STAGES else None
    veg = body.veg_freq if body.veg_freq in VEG_FREQ else None
    sleep = body.sleep_pattern if body.sleep_pattern in SLEEP_PATTERN else None
    exercise = body.exercise_freq if body.exercise_freq in EXERCISE_FREQ else None

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # 旧 current 置为 false
        await conn.execute(
            "UPDATE health_profiles SET is_current = FALSE WHERE user_id = $1 AND is_current = TRUE",
            user_id,
        )
        # 顺便清理 90 天前的旧快照
        await conn.execute(
            """
            DELETE FROM health_profiles
             WHERE user_id = $1
               AND is_current = FALSE
               AND created_at < now() - interval '90 days'
            """,
            user_id,
        )

        row = await conn.fetchrow(
            f"""
            INSERT INTO health_profiles
                (user_id, is_current, gender, height_cm, weight_kg, bmi, bmi_band,
                 goals, allergies, dislikes, prenatal_stage,
                 veg_freq, sleep_pattern, exercise_freq, taste_prefer)
            VALUES ($1, TRUE, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14)
            RETURNING {SELECT_COLS}
            """,
            user_id,
            body.gender,
            body.height_cm,
            body.weight_kg,
            bmi,
            _bmi_band(bmi),
            body.goals,
            allergies,
            dislikes,
            stage or None,
            veg,
            sleep,
            exercise,
            taste_prefer,
        )
    return _row_to_profile(row)
