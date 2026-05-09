from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import auth as auth_svc

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class RequestCodeBody(BaseModel):
    phone: str = Field(min_length=6, max_length=20)


class VerifyCodeBody(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    code: str = Field(min_length=4, max_length=8)


@router.post("/request-code")
async def request_code(body: RequestCodeBody) -> dict:
    try:
        return await auth_svc.request_code(body.phone)
    except auth_svc.AuthError as e:
        raise HTTPException(status_code=e.http, detail={"code": e.code, "message": str(e)})


@router.post("/verify-code")
async def verify_code(body: VerifyCodeBody) -> dict:
    try:
        return await auth_svc.verify_code(body.phone, body.code)
    except auth_svc.AuthError as e:
        raise HTTPException(status_code=e.http, detail={"code": e.code, "message": str(e)})
