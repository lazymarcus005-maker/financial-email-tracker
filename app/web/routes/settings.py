"""Settings route - read-only view of non-secret configuration."""

import logging
import json
import secrets

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import Settings, get_settings
from app.gmail.authorize import (
    build_authorization_url,
    exchange_authorization_response,
    token_exists,
    user_token_path,
)
from app.storage import queries
from app.web.deps import get_current_user_id, get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])
page_router = APIRouter(tags=["settings-pages"])
GMAIL_OAUTH_STATE_COOKIE = "fet_gmail_oauth_state"


def _public_url_for(request: Request, route_name: str, settings: Settings) -> str:
    path = request.url_for(route_name).path
    if settings.PUBLIC_BASE_URL:
        return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"
    return str(request.url_for(route_name))


def _safe_settings(settings: Settings) -> dict:
    """Return settings safe to show in the UI - no tokens/credentials."""
    return {
        "gmail_query": settings.GMAIL_QUERY,
        "database_path": settings.DATABASE_PATH,
        "schedule": settings.SCHEDULE,
        "timezone": settings.TIMEZONE,
        "ai_enabled": settings.AI_ENABLED,
        "ollama_base_url": settings.OLLAMA_BASE_URL,
        "ollama_model": settings.OLLAMA_MODEL,
        "parser_version": settings.PARSER_VERSION,
        "line_configured": bool(settings.LINE_CHANNEL_ACCESS_TOKEN and settings.LINE_USER_ID),
        "log_level": settings.LOG_LEVEL,
    }


@router.get("/settings")
async def get_settings_view(
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    data = _safe_settings(settings)
    data["gmail_connected"] = token_exists(user_token_path(owner_user_id))
    return data


@router.post("/settings/clear-data")
async def clear_all_data(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    counts = await queries.clear_runtime_data(db, owner_user_id=owner_user_id)
    if request.headers.get("HX-Request") == "true":
        deleted = sum(counts.values())
        return HTMLResponse(
            f'<div id="settings-data-status" class="text-sm text-green-600">Cleared {deleted} rows.</div>'
        )
    return {"cleared": counts}


@router.get("/settings/export")
async def export_data(
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    payload = await queries.export_runtime_data(db, owner_user_id=owner_user_id)
    return JSONResponse(
        payload,
        headers={"Content-Disposition": 'attachment; filename="financial-email-tracker-export.json"'},
    )


@router.post("/settings/import")
async def import_data(
    request: Request,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    try:
        raw = await file.read()
        payload = json.loads(raw.decode("utf-8"))
        imported = await queries.import_runtime_data(db, payload, replace=True, owner_user_id=owner_user_id)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if request.headers.get("HX-Request") == "true":
        total = sum(imported.values())
        return HTMLResponse(
            f'<div id="settings-data-status" class="text-sm text-green-600">Imported {total} rows.</div>'
        )
    return {"imported": imported}


@router.get("/gmail/status")
async def gmail_status(owner_user_id: int = Depends(get_current_user_id)):
    return {"connected": token_exists(user_token_path(owner_user_id))}


@router.post("/gmail/disconnect")
async def gmail_disconnect(request: Request, owner_user_id: int = Depends(get_current_user_id)):
    token_path = user_token_path(owner_user_id)
    if token_path.exists():
        token_path.unlink()
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse(
            '<div id="gmail-status" class="text-sm text-amber-700">Gmail disconnected.</div>'
        )
    return {"connected": False}


@page_router.get("/gmail/connect")
async def gmail_connect(request: Request, settings: Settings = Depends(get_settings)):
    state = secrets.token_urlsafe(24)
    redirect_uri = _public_url_for(request, "gmail_oauth_callback", settings)
    try:
        authorization_url = build_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            credentials_path=settings.GMAIL_CREDENTIALS_PATH,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    response = RedirectResponse(authorization_url, status_code=303)
    response.set_cookie(GMAIL_OAUTH_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax")
    return response


@page_router.get("/gmail/oauth2/callback")
async def gmail_oauth_callback(
    request: Request,
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    expected_state = request.cookies.get(GMAIL_OAUTH_STATE_COOKIE)
    received_state = request.query_params.get("state")
    if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
        raise HTTPException(status_code=400, detail="Invalid Gmail OAuth state")

    redirect_uri = _public_url_for(request, "gmail_oauth_callback", settings)
    try:
        exchange_authorization_response(
            redirect_uri=redirect_uri,
            authorization_response=str(request.url),
            credentials_path=settings.GMAIL_CREDENTIALS_PATH,
            token_path=user_token_path(owner_user_id),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gmail authorization failed: {e}") from e

    response = RedirectResponse("/settings", status_code=303)
    response.delete_cookie(GMAIL_OAUTH_STATE_COOKIE)
    return response


@page_router.get("/settings")
async def settings_page(
    request: Request,
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    safe_settings = _safe_settings(settings)
    safe_settings["gmail_connected"] = token_exists(user_token_path(owner_user_id))
    return templates.TemplateResponse(request, "settings.html", {"settings": safe_settings})
