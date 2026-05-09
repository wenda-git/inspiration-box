from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.db import get_pool
from app.deps import current_user_id

router = APIRouter(prefix="/v1/addresses", tags=["addresses"])


# ---------- schemas ----------

class AddressIn(BaseModel):
    receiver_name: str = Field(min_length=1, max_length=40)
    receiver_phone: str = Field(min_length=6, max_length=20)
    region: str = Field(min_length=2, max_length=80, description="'广东省/广州市/越秀区'")
    detail: str = Field(min_length=2, max_length=200)
    is_default: bool = False


class Address(AddressIn):
    id: UUID
    created_at: datetime
    updated_at: datetime


# ---------- SQL helpers ----------

SELECT_COLS = (
    "id, receiver_name, receiver_phone, region, detail, is_default, "
    "created_at, updated_at"
)


def _row_to_addr(row) -> Address:
    return Address(
        id=row["id"],
        receiver_name=row["receiver_name"],
        receiver_phone=row["receiver_phone"],
        region=row["region"],
        detail=row["detail"],
        is_default=row["is_default"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _clear_defaults(conn, user_id: UUID, except_id: UUID | None = None) -> None:
    await conn.execute(
        """
        UPDATE addresses
           SET is_default = FALSE, updated_at = now()
         WHERE user_id = $1
           AND is_default = TRUE
           AND deleted_at IS NULL
           AND ($2::uuid IS NULL OR id <> $2)
        """,
        user_id, except_id,
    )


# ---------- endpoints ----------

@router.get("", response_model=list[Address])
async def list_addresses(
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> list[Address]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {SELECT_COLS}
              FROM addresses
             WHERE user_id = $1 AND deleted_at IS NULL
             ORDER BY is_default DESC, created_at
            """,
            UUID(user_id_str),
        )
    return [_row_to_addr(r) for r in rows]


@router.post("", response_model=Address, status_code=status.HTTP_201_CREATED)
async def create_address(
    body: AddressIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Address:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        has_any = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM addresses WHERE user_id = $1 AND deleted_at IS NULL)",
            user_id,
        )
        make_default = body.is_default or not has_any
        if make_default:
            await _clear_defaults(conn, user_id)

        row = await conn.fetchrow(
            f"""
            INSERT INTO addresses
                (user_id, receiver_name, receiver_phone, region, detail, is_default)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {SELECT_COLS}
            """,
            user_id,
            body.receiver_name,
            body.receiver_phone,
            body.region,
            body.detail,
            make_default,
        )
    return _row_to_addr(row)


@router.put("/{address_id}", response_model=Address)
async def update_address(
    address_id: UUID,
    body: AddressIn,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Address:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            "SELECT id FROM addresses WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            address_id, user_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})

        if body.is_default:
            await _clear_defaults(conn, user_id, except_id=address_id)

        row = await conn.fetchrow(
            f"""
            UPDATE addresses
               SET receiver_name = $1,
                   receiver_phone = $2,
                   region = $3,
                   detail = $4,
                   is_default = $5,
                   updated_at = now()
             WHERE id = $6
            RETURNING {SELECT_COLS}
            """,
            body.receiver_name,
            body.receiver_phone,
            body.region,
            body.detail,
            body.is_default,
            address_id,
        )
    return _row_to_addr(row)


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_address(
    address_id: UUID,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Response:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            """
            SELECT id, is_default
              FROM addresses
             WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            """,
            address_id, user_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})

        await conn.execute(
            """
            UPDATE addresses SET deleted_at = now(), is_default = FALSE
             WHERE id = $1
            """,
            address_id,
        )

        # 若删掉的是默认，自动补一个最早的做默认
        if existing["is_default"]:
            await conn.execute(
                """
                UPDATE addresses
                   SET is_default = TRUE, updated_at = now()
                 WHERE id = (
                    SELECT id FROM addresses
                     WHERE user_id = $1 AND deleted_at IS NULL
                     ORDER BY created_at
                     LIMIT 1
                 )
                """,
                user_id,
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{address_id}/default", response_model=Address)
async def set_default(
    address_id: UUID,
    user_id_str: Annotated[str, Depends(current_user_id)],
) -> Address:
    user_id = UUID(user_id_str)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            "SELECT id FROM addresses WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            address_id, user_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})

        await _clear_defaults(conn, user_id, except_id=address_id)
        row = await conn.fetchrow(
            f"""
            UPDATE addresses
               SET is_default = TRUE, updated_at = now()
             WHERE id = $1
            RETURNING {SELECT_COLS}
            """,
            address_id,
        )
    return _row_to_addr(row)
