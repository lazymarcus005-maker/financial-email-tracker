"""Admin user management routes."""

import sqlite3

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.storage import queries
from app.web.auth import hash_password, require_admin
from app.web.deps import get_db, templates

router = APIRouter(prefix="/api", tags=["users"])
page_router = APIRouter(tags=["users-pages"])


async def _payload(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return dict(form)
    return await request.json()


def _bool_value(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on", "active")


def _role(value: str | None) -> str:
    return value if value in ("admin", "user") else "user"


async def _assert_not_last_admin(
    db: aiosqlite.Connection,
    existing: dict,
    new_role: str | None = None,
    new_is_active: bool | None = None,
) -> None:
    role = new_role if new_role is not None else existing["role"]
    is_active = new_is_active if new_is_active is not None else existing["is_active"]
    if existing["role"] == "admin" and existing["is_active"] and (role != "admin" or not is_active):
        if await queries.count_active_admins(db) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove or disable the last active admin")


@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(db: aiosqlite.Connection = Depends(get_db)):
    return {"items": await queries.list_users(db)}


@router.post("/users", dependencies=[Depends(require_admin)], status_code=201)
async def create_user(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    body = await _payload(request)
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    try:
        user = await queries.create_user(
            db,
            email=str(body.get("email") or ""),
            display_name=str(body.get("display_name") or body.get("email") or ""),
            password_hash=hash_password(password),
            role=_role(body.get("role")),
            is_active=_bool_value(body.get("is_active", True)),
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="Email is already in use") from e

    if request.headers.get("HX-Request") == "true":
        users = await queries.list_users(db)
        return templates.TemplateResponse(request, "partials/users_table.html", {"users": users})

    # New admin-created user changes whether the table is non-empty, so
    # drop the auth-middleware cache to keep the next request honest.
    from app.web.main import invalidate_user_count_cache
    invalidate_user_count_cache()
    return user


@router.patch("/users/{user_id}", dependencies=[Depends(require_admin)])
async def update_user(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    existing = await queries.get_user(db, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    body = await _payload(request)
    role = _role(body.get("role")) if "role" in body else existing["role"]
    is_active = _bool_value(body.get("is_active")) if "is_active" in body else existing["is_active"]
    await _assert_not_last_admin(db, existing, new_role=role, new_is_active=is_active)

    user = await queries.update_user(
        db,
        user_id=user_id,
        display_name=str(body.get("display_name") or existing["display_name"]),
        role=role,
        is_active=is_active,
    )
    if request.headers.get("HX-Request") == "true":
        users = await queries.list_users(db)
        return templates.TemplateResponse(request, "partials/users_table.html", {"users": users})
    return user


@router.post("/users/{user_id}/password", dependencies=[Depends(require_admin)])
async def reset_password(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    existing = await queries.get_user(db, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    body = await _payload(request)
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    await queries.update_user_password(db, user_id, hash_password(password))

    if request.headers.get("HX-Request") == "true":
        return HTMLResponse('<div id="users-status" class="text-sm text-green-600">Password updated.</div>')
    return {"status": "ok"}


@page_router.get("/users", dependencies=[Depends(require_admin)])
async def users_page(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    users = await queries.list_users(db)
    return templates.TemplateResponse(request, "users.html", {"users": users})
