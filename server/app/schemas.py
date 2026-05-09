from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- LLM 输入 ----------

class UserProfile(BaseModel):
    user_id: UUID
    goals: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    recent_negative_feedback: list[str] = Field(
        default_factory=list,
        description="本周应回避的水果 fruit_code 列表",
    )


class InventoryItem(BaseModel):
    fruit_code: str
    batch_id: str
    qty_avail_g: int
    nutrition_per_100g: dict[str, float]
    unit_cost_cny_per_kg: float
    best_before_days: int
    serving_unit: str = "g"
    serving_size_g: int = 500
    origin_region: str = ""
    season_months: list[int] = Field(default_factory=list)
    allergy_tags: list[str] = Field(default_factory=list)
    value_tier: str = "basic"
    selection_status: str = "normal"
    ops_weight: int = 0


class RecommendLLMInput(BaseModel):
    user_profile: UserProfile
    plan_code: Literal["fatloss_gi", "prenatal", "antiox"]
    box_target_g: int = 2000
    budget_cny: float = 105.0
    inventory_snapshot: list[InventoryItem]


# ---------- LLM 输出（structured output schema 源） ----------

class RecommendItem(BaseModel):
    fruit_code: str
    batch_id: str
    qty_g: int = Field(ge=150, le=2000)
    reason: str = Field(min_length=15, max_length=120)


class NutritionReport(BaseModel):
    total_g: int
    weighted_avg_per_100g: dict[str, float]
    hits: list[str]
    gaps: list[str]


class RecommendLLMOutput(BaseModel):
    items: list[RecommendItem]
    nutrition_report: NutritionReport
    guide_md: str
    total_cost_cny: float


# ---------- HTTP API ----------

class RecommendRequest(BaseModel):
    subscription_id: UUID
    iso_year: int
    iso_week: int = Field(ge=1, le=53)
    regenerate: bool = False


class RecommendResponseItem(RecommendItem):
    fruit_name_cn: str
    unit_price_cny_per_kg: float


class RecommendResponse(BaseModel):
    recommendation_id: UUID
    subscription_id: UUID
    plan_code: str
    iso_year: int
    iso_week: int
    items: list[RecommendResponseItem]
    nutrition_report: NutritionReport
    guide_md: str
    total_cost_cny: float
    cached: bool
    generated_at: datetime
