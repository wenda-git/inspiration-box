from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.admin.deps import NotLoggedIn
from app.admin.fruits import router as admin_fruits_router
from app.admin.inventory import router as admin_inventory_router
from app.admin.orders import router as admin_orders_router
from app.admin.plans import router as admin_plans_router
from app.admin.routes import router as admin_dashboard_router
from app.api.addresses import router as addresses_router
from app.api.assets import router as assets_router
from app.api.auth import router as auth_router
from app.api.feedback import router as feedback_router
from app.api.me import router as me_router
from app.api.orders import router as orders_router
from app.api.plans import router as plans_router
from app.api.profile import router as profile_router
from app.api.recommend import router as recommend_router
from app.api.share import router as share_router
from app.api.subscriptions import router as subscriptions_router
from app.api.uploads import router as uploads_router
from app.api.webhooks import router as webhooks_router
from app.db import close_pool, get_pool


def _setup_dev_file_logging() -> None:
    log_file = os.getenv("VITALENS_LOG_FILE", "/tmp/vitalens-uvicorn.log")
    root = logging.getLogger()
    if any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", None) == log_file
        for handler in root.handlers
    ):
        return
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root.addHandler(handler)
    root.setLevel(min(root.level or logging.INFO, logging.INFO))


_setup_dev_file_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()            # 进程启动预热连接池
    yield
    await close_pool()


app = FastAPI(title="Inspiration Box API", version="0.1.0", lifespan=lifespan)
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/uploads",
    StaticFiles(directory=str(UPLOAD_DIR)),
    name="uploads",
)


@app.exception_handler(NotLoggedIn)
async def _not_logged_in_handler(request, exc):
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/admin/login", status_code=303)


# 把 Pydantic 的英文校验错误转成用户能看懂的中文
_FIELD_CN = {
    "receiver_name": "收件人",
    "receiver_phone": "手机号",
    "region": "省市区",
    "detail": "详细地址",
    "phone": "手机号",
    "code": "验证码",
    "height_cm": "身高",
    "weight_kg": "体重",
    "gender": "性别",
    "plan_code": "健康方向",
    "goals": "健康目标",
}


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    if not errs:
        msg = "参数校验失败"
    else:
        err = errs[0]
        loc = [str(x) for x in err.get("loc", []) if x != "body"]
        field = loc[-1] if loc else "参数"
        field_cn = _FIELD_CN.get(field, field)
        t = err.get("type", "")
        ctx = err.get("ctx") or {}
        if t.startswith("string_too_short"):
            msg = f"{field_cn}至少 {ctx.get('min_length', '?')} 个字"
        elif t.startswith("string_too_long"):
            msg = f"{field_cn}最多 {ctx.get('max_length', '?')} 个字"
        elif t.startswith("missing"):
            msg = f"请填写{field_cn}"
        elif t.startswith("greater_than_equal") or t == "value_error.number.not_ge":
            msg = f"{field_cn}不能小于 {ctx.get('ge', '?')}"
        elif t.startswith("less_than_equal"):
            msg = f"{field_cn}不能大于 {ctx.get('le', '?')}"
        elif t.startswith("string_pattern_mismatch"):
            msg = f"{field_cn}格式不对"
        else:
            msg = err.get("msg", "参数校验失败")

    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "VALIDATION_FAILED", "message": msg}},
    )


app.include_router(auth_router)
app.include_router(assets_router)
app.include_router(me_router)
app.include_router(profile_router)
app.include_router(addresses_router)
app.include_router(plans_router)
app.include_router(subscriptions_router)
app.include_router(recommend_router)
app.include_router(feedback_router)
app.include_router(orders_router)
app.include_router(share_router)
app.include_router(uploads_router)
app.include_router(webhooks_router)
app.include_router(admin_dashboard_router)
app.include_router(admin_fruits_router)
app.include_router(admin_inventory_router)
app.include_router(admin_plans_router)
app.include_router(admin_orders_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
