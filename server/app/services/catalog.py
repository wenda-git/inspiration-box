"""
从 DB 读取水果主档 / 库存 / 箱型策略。

进程级内存缓存（60 秒），避免每次 /recommend 都查三张表。
写操作（后台 CRUD）调用 invalidate() 主动失效。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.db import get_pool
from app.schemas import InventoryItem


def _parse_jsonb(v: Any) -> Any:
    """asyncpg 有时返回 dict，有时返回 str；统一成 Python 对象"""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    return json.loads(v)


_CACHE_TTL = 60
_cache: dict[str, tuple[float, Any]] = {}


def invalidate(key: str | None = None) -> None:
    """写入后台数据后调用，防止前台读旧缓存"""
    global _cache
    if key is None:
        _cache = {}
    else:
        _cache.pop(key, None)


async def _cached(key: str, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = await loader()
    _cache[key] = (now + _CACHE_TTL, value)
    return value


# ---------- 数据结构 ----------

@dataclass
class FruitMaster:
    code: str
    name_cn: str
    emoji: str
    category: str
    nutrition: dict[str, float]
    plan_affinity: dict[str, int]
    forbidden_for: list[str]
    tags: list[str]
    is_active: bool
    serving_unit: str = "g"
    serving_size_g: int = 500
    origin: str = ""
    origin_region: str = ""         # 'south_china'/'north_china'/'imported'
    season_months: list[int] = field(default_factory=list)   # [] = 全年
    allergy_tags: list[str] = field(default_factory=list)
    value_tier: str = "basic"       # basic / highlight / premium
    selection_status: str = "normal" # normal / featured / downranked / paused
    ops_weight: int = 0
    ops_note: str = ""


@dataclass
class PlanRule:
    code: str
    name: str
    tagline: str
    theme: str
    emoji: str
    price_cny: float
    budget_cny: float
    box_target_g: int
    hard_rules: dict
    soft_prefs: dict
    is_active: bool


# ---------- 查询 ----------

_NUTRITION_KEYS = (
    "kcal", "carb_g", "sugar_g", "fiber_g", "protein_g", "fat_g",
    "gi", "vitC_mg", "folate_ug", "anthocyanin_mg", "potassium_mg",
    "calcium_mg", "iron_mg", "magnesium_mg", "fructose_g", "orac_umol_te",
)

_FRUIT_COLS = (
    ", ".join(_NUTRITION_KEYS)
    + ", serving_unit, serving_size_g, origin, origin_region, season_months,"
    + " allergy_tags, value_tier, selection_status, ops_weight, ops_note"
)


def _row_to_fruit(row) -> FruitMaster:
    nutrition = {}
    for k in _NUTRITION_KEYS:
        v = row[k.lower()]
        if v is not None:
            nutrition[k] = float(v)
    return FruitMaster(
        code=row["code"],
        name_cn=row["name_cn"],
        emoji=row["emoji"] or "🍎",
        category=row["category"] or "",
        nutrition=nutrition,
        plan_affinity=_parse_jsonb(row["plan_affinity"]) or {},
        forbidden_for=list(row["forbidden_for"] or []),
        tags=list(row["tags"] or []),
        is_active=row["is_active"],
        serving_unit=row["serving_unit"] or "g",
        serving_size_g=int(row["serving_size_g"] or 500),
        origin=row["origin"] or "",
        origin_region=row["origin_region"] or "",
        season_months=list(row["season_months"] or []),
        allergy_tags=list(row["allergy_tags"] or []),
        value_tier=row["value_tier"] or "basic",
        selection_status=row["selection_status"] or "normal",
        ops_weight=int(row["ops_weight"] or 0),
        ops_note=row["ops_note"] or "",
    )


async def load_fruits() -> dict[str, FruitMaster]:
    """按 code 索引的全量活跃水果"""
    async def _loader():
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT code, name_cn, emoji, category,
                       {_FRUIT_COLS},
                       plan_affinity, forbidden_for, tags, is_active
                  FROM fruits
                 WHERE is_active
                """
            )
        return {r["code"]: _row_to_fruit(r) for r in rows}
    return await _cached("fruits", _loader)


async def load_plans() -> dict[str, PlanRule]:
    async def _loader():
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT code, name, tagline, theme, emoji,
                       price_cny, budget_cny, box_target_g,
                       hard_rules, soft_prefs, is_active
                  FROM subscription_plans
                 WHERE is_active
                 ORDER BY sort_order
                """
            )
        out = {}
        for r in rows:
            out[r["code"]] = PlanRule(
                code=r["code"], name=r["name"],
                tagline=r["tagline"] or "", theme=r["theme"] or "matcha",
                emoji=r["emoji"] or "🍎",
                price_cny=float(r["price_cny"]),
                budget_cny=float(r["budget_cny"]),
                box_target_g=int(r["box_target_g"]),
                hard_rules=_parse_jsonb(r["hard_rules"]) or {},
                soft_prefs=_parse_jsonb(r["soft_prefs"]) or {},
                is_active=r["is_active"],
            )
        return out
    return await _cached("plans", _loader)


async def load_inventory() -> list[InventoryItem]:
    """
    当前可用库存 → InventoryItem 列表（给 selector 吃）。
    按 best_before 升序，近效期优先。
    """
    async def _loader():
        fruits = await load_fruits()
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT b.fruit_code, b.batch_no, b.qty_avail_g,
                       b.unit_cost_cny_per_kg, b.best_before, b.arrived_on
                  FROM inventory_batches b
                 WHERE b.is_active AND b.qty_avail_g > 0
                 ORDER BY b.best_before ASC
                """
            )
        out: list[InventoryItem] = []
        today = None
        for r in rows:
            fruit = fruits.get(r["fruit_code"])
            if fruit is None:
                continue   # 孤儿批次（水果下架了），跳过
            best_before = r["best_before"]
            # best_before_days 从 arrived_on 推导：简单起见用剩余天数
            from datetime import date
            today = today or date.today()
            days_left = (best_before - today).days
            out.append(InventoryItem(
                fruit_code=r["fruit_code"],
                batch_id=r["batch_no"],
                qty_avail_g=int(r["qty_avail_g"]),
                nutrition_per_100g=fruit.nutrition,
                unit_cost_cny_per_kg=float(r["unit_cost_cny_per_kg"]),
                best_before_days=max(0, days_left),
                serving_unit=fruit.serving_unit,
                serving_size_g=fruit.serving_size_g,
                origin_region=fruit.origin_region,
                season_months=fruit.season_months,
                allergy_tags=fruit.allergy_tags,
                value_tier=fruit.value_tier,
                selection_status=fruit.selection_status,
                ops_weight=fruit.ops_weight,
            ))
        return out
    return await _cached("inventory", _loader)


async def fruit_display_map() -> dict[str, dict[str, str]]:
    """orchestrator 补 UI 字段时用"""
    fruits = await load_fruits()
    return {code: {"name_cn": f.name_cn, "emoji": f.emoji} for code, f in fruits.items()}
