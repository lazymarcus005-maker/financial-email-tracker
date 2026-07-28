"""Authentication and first-admin setup routes."""

import sqlite3

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.storage import queries
from app.web.auth import clear_login_cookie, hash_password, set_login_cookie, verify_password
from app.web.deps import get_db, templates

router = APIRouter(tags=["auth"])


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@router.get("/login")
async def login_page(request: Request, next: str | None = None):
    if getattr(request.state, "current_user", None):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    user = await queries.get_user_by_email(db, email)
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": _safe_next(next), "error": "Invalid email or password."},
            status_code=400,
        )

    response = RedirectResponse(_safe_next(next), status_code=303)
    set_login_cookie(response, user["id"])
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    clear_login_cookie(response)
    return response


@router.get("/setup")
async def setup_page(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    if await queries.count_users(db) > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def setup(
    request: Request,
    email: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    if await queries.count_users(db) > 0:
        raise HTTPException(status_code=409, detail="Setup has already been completed")
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": "Password must be at least 8 characters."},
            status_code=400,
        )

    try:
        user = await queries.create_user(
            db,
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": "That email is already in use."},
            status_code=400,
        )

    response = RedirectResponse("/", status_code=303)
    await queries.claim_unowned_runtime_data(db, user["id"])
    set_login_cookie(response, user["id"])
    return response
