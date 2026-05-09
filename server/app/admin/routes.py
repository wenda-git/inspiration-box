"""
后台路由入口 + 登录 + dashboard。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import (
    COOKIE_NAME,
    make_session_cookie,
    read_session,
    require_admin,
)
from app.config import get_settings
from app.db import get_pool


router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    """在每个模板里注入 request.state.admin_user，避免 None 报错"""
    # 登录页不会有 admin_user 也没事，layout 里有 if 判断
    if not hasattr(request.state, "admin_user"):
        request.state.admin_user = None
    return templates.TemplateResponse(request, name, ctx)


# ---------- 登录 / 登出 ----------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    # 已登录就跳 dashboard
    token = request.cookies.get(COOKIE_NAME)
    if read_session(token):
        return RedirectResponse("/admin", status_code=302)
    request.state.admin_user = None
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
async def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    s = get_settings()
    if username == s.admin_username and password == s.admin_password:
        token = make_session_cookie(username)
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=86400)
        return resp
    request.state.admin_user = None
    return templates.TemplateResponse(
        request, "login.html",
        {"error": "用户名或密码不对"},
        status_code=401,
    )


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------- 守卫下的页面 ----------

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, _: str = Depends(require_admin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        active_fruits = await conn.fetchval("SELECT count(*) FROM fruits WHERE is_active")
        active_batches = await conn.fetchval("SELECT count(*) FROM inventory_batches WHERE is_active AND qty_avail_g > 0")
        reserved_g = await conn.fetchval(
            "SELECT COALESCE(sum(qty_g), 0) FROM order_inventory_reservations WHERE status = 'reserved'"
        )
        users = await conn.fetchval("SELECT count(*) FROM users WHERE deleted_at IS NULL")
        active_subs = await conn.fetchval(
            "SELECT count(*) FROM subscriptions WHERE status IN ('trial','active')"
        )
        paused_subs = await conn.fetchval(
            "SELECT count(*) FROM subscriptions WHERE status = 'paused'"
        )
        paid_subs = await conn.fetchval("SELECT count(*) FROM subscriptions WHERE billing_state = 'paid'")
        recent_users = await conn.fetch("""
            SELECT phone_e164, created_at
              FROM users
             WHERE created_at > now() - interval '7 days'
             ORDER BY created_at DESC
             LIMIT 8
        """)
        llm = await conn.fetchrow("""
            SELECT count(*) AS total,
                   COALESCE(round(avg(latency_ms))::int, 0) AS avg_ms,
                   COALESCE(sum(tokens_in), 0) AS tokens_in,
                   COALESCE(sum(tokens_out), 0) AS tokens_out
              FROM llm_call_logs
             WHERE created_at > now() - interval '7 days'
        """)
        ops = await conn.fetchrow("""
            SELECT
              COUNT(*) FILTER (WHERE o.status = 'locked') AS locked,
              COUNT(*) FILTER (WHERE o.status = 'packing') AS packing,
              COUNT(*) FILTER (WHERE o.status = 'shipped') AS shipped,
              COUNT(*) FILTER (WHERE o.status = 'delivered') AS delivered,
              COUNT(*) FILTER (WHERE o.status = 'cancelled') AS cancelled,
              COALESCE(sum(o.total_cost_cny), 0) AS order_cost,
              COALESCE(sum(COALESCE((sp.soft_prefs->>'estimated_ops_cost_cny')::numeric, 0)), 0) AS ops_cost_estimate
              FROM orders o
              LEFT JOIN subscription_plans sp ON sp.code = o.plan_code
             WHERE o.created_at > now() - interval '14 days'
        """)
        revenue = await conn.fetchrow("""
            SELECT COALESCE(sum(amount_cny) FILTER (WHERE status='success'), 0) AS revenue,
                   COUNT(*) FILTER (WHERE status='success') AS paid_count,
                   COUNT(*) FILTER (WHERE status='pending') AS pending_count
              FROM payments
             WHERE created_at > now() - interval '14 days'
        """)
        feedback = await conn.fetchrow("""
            SELECT COUNT(*) AS total,
                   COALESCE(round(avg(rating)::numeric, 2), 0) AS avg_rating,
                   COUNT(*) FILTER (WHERE rating <= 2) AS low_rating
              FROM feedbacks
             WHERE created_at > now() - interval '30 days'
        """)
        disliked = await conn.fetch("""
            SELECT COALESCE(fr.name_cn, f.fruit_id) AS fruit_name_cn, count(*) AS cnt
              FROM feedbacks f
              LEFT JOIN fruits fr ON fr.code = f.fruit_id
             WHERE f.rating <= 2 AND f.fruit_id IS NOT NULL
             GROUP BY COALESCE(fr.name_cn, f.fruit_id)
             ORDER BY cnt DESC
             LIMIT 5
        """)

    iso_year, iso_week, _ = datetime.now(timezone.utc).isocalendar()
    ops_dict = dict(ops) if ops else {}
    revenue_dict = dict(revenue) if revenue else {}
    ops_dict["contribution_estimate"] = round(
        float(revenue_dict.get("revenue") or 0)
        - float(ops_dict.get("order_cost") or 0)
        - float(ops_dict.get("ops_cost_estimate") or 0),
        2,
    )
    return render(request, "dashboard.html",
        stats={
            "active_fruits": active_fruits,
            "active_batches": active_batches,
            "reserved_kg": round(float(reserved_g or 0) / 1000, 1),
            "users": users,
            "active_subs": active_subs,
            "paused_subs": paused_subs,
            "paid_subs": paid_subs,
            "conversion": round((float(paid_subs or 0) / max(int(users or 0), 1)) * 100, 1),
        },
        recent_users=[
            {"phone": _mask_phone(r["phone_e164"]), "at": r["created_at"].strftime("%m-%d %H:%M")}
            for r in recent_users
        ],
        llm=dict(llm) if llm else {"total": 0, "avg_ms": 0, "tokens_in": 0, "tokens_out": 0},
        ops=ops_dict,
        revenue=revenue_dict,
        feedback=dict(feedback) if feedback else {},
        disliked=[dict(r) for r in disliked],
        iso_year=iso_year, iso_week=iso_week,
    )


def _mask_phone(p: str | None) -> str:
    if not p:
        return "-"
    tail = p[-11:]
    return tail[:3] + "****" + tail[-4:]
