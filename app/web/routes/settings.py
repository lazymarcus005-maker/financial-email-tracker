"""Settings route - read-only view of non-secret configuration."""

import json
import logging
import secrets
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import Settings, get_settings
from app.gmail.authorize import (
    GmailReauthorizationRequired,
    build_authorization_url,
    exchange_authorization_response,
    token_exists,
    user_token_path,
)
from app.gmail.client import GmailClient
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


def _public_authorization_response(request: Request, redirect_uri: str) -> str:
    if request.url.query:
        return f"{redirect_uri}?{request.url.query}"
    return redirect_uri


def _masked_gmail_client_id(client_id: str | None) -> str:
    if not client_id:
        return "client_id not set"
    if len(client_id) <= 20:
        return client_id
    return f"{client_id[:8]}...{client_id[-16:]}"


def _read_token_info(token_path: Path) -> dict | None:
    """Return non-secret fields from the Gmail OAuth token JSON."""
    if not token_path.exists():
        return None
    try:
        data = json.loads(token_path.read_text())
        return {
            "scopes": data.get("scopes", []),
            "expiry": data.get("expiry"),
            "has_refresh_token": bool(data.get("refresh_token")),
        }
    except Exception:
        return None


def _gmail_status(owner_user_id: int) -> tuple[bool, str | None]:
    token_path = user_token_path(owner_user_id)
    if not token_exists(token_path):
        return False, None
    try:
        return True, GmailClient(token_path=token_path).get_profile_email()
    except GmailReauthorizationRequired:
        logger.info("Gmail authorization requires reconnect for user %s", owner_user_id)
        return False, None


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
        "mcp_enabled": settings.MCP_ENABLED,
        "mcp_transport": settings.MCP_TRANSPORT,
        "mcp_host": settings.MCP_HOST,
        "mcp_port": settings.MCP_PORT,
        "mcp_owner_user_id": settings.MCP_OWNER_USER_ID,
        "mcp_allow_write": settings.MCP_ALLOW_WRITE,
        "mcp_api_token_set": bool(settings.MCP_API_TOKEN),
    }


@router.get("/settings")
async def get_settings_view(
    request: Request,
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    data = _safe_settings(settings)
    data["gmail_connected"], data["gmail_profile_email"] = _gmail_status(owner_user_id)
    data["gmail_redirect_uri"] = _public_url_for(request, "gmail_oauth_callback", settings)
    data["gmail_oauth_client_id"] = _masked_gmail_client_id(settings.GMAIL_CLIENT_ID)
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
async def gmail_status(
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    token_path = user_token_path(owner_user_id)
    connected, profile_email = _gmail_status(owner_user_id)
    return {"connected": connected, "profile_email": profile_email}


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
    logger.info("Starting Gmail OAuth connect with redirect_uri=%s", redirect_uri)
    try:
        authorization_url = build_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            client_id=settings.GMAIL_CLIENT_ID,
            client_secret=settings.GMAIL_CLIENT_SECRET,
        )
    except ValueError as e:
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
    authorization_response = _public_authorization_response(request, redirect_uri)
    try:
        exchange_authorization_response(
            redirect_uri=redirect_uri,
            authorization_response=authorization_response,
            token_path=user_token_path(owner_user_id),
            client_id=settings.GMAIL_CLIENT_ID,
            client_secret=settings.GMAIL_CLIENT_SECRET,
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
    token_path = user_token_path(owner_user_id)
    safe_settings["gmail_connected"], safe_settings["gmail_profile_email"] = _gmail_status(owner_user_id)
    safe_settings["gmail_redirect_uri"] = _public_url_for(request, "gmail_oauth_callback", settings)
    safe_settings["gmail_oauth_client_id"] = _masked_gmail_client_id(settings.GMAIL_CLIENT_ID)
    safe_settings["gmail_token_path"] = str(token_path)
    safe_settings["gmail_token_info"] = _read_token_info(token_path)
    return templates.TemplateResponse(request, "settings.html", {"settings": safe_settings})
