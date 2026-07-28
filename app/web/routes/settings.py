"""Settings route - read-only view of non-secret configuration."""

import logging
import json
import secrets
from pathlib import Path

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
from app.gmail.client import GmailClient
from app.integrations.line import send_message
from app.storage import queries
from app.web.deps import get_current_user_id, get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])
page_router = APIRouter(tags=["settings-pages"])
GMAIL_OAUTH_STATE_COOKIE = "fet_gmail_oauth_state"

# User settings keys
SETTING_KEY_GMAIL_QUERY = "gmail_query"
SETTING_KEY_SCHEDULE = "schedule"


async def _get_user_setting_or_default(
    db: aiosqlite.Connection,
    key: str,
    default: str,
    owner_user_id: int | None,
) -> str:
    """Get user setting from DB, fall back to default if not set."""
    value = await queries.get_user_setting(db, key, owner_user_id=owner_user_id)
    return value if value is not None else default


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


async def _load_user_settings(db: aiosqlite.Connection, settings: Settings, owner_user_id: int | None) -> dict:
    """Load user-overridden settings from database, with fallback to config."""
    gmail_query = await _get_user_setting_or_default(
        db, SETTING_KEY_GMAIL_QUERY, settings.GMAIL_QUERY, owner_user_id
    )
    schedule_raw = await _get_user_setting_or_default(
        db, SETTING_KEY_SCHEDULE, ",".join(settings.SCHEDULE), owner_user_id
    )
    schedule = [s.strip() for s in schedule_raw.split(",") if s.strip()]
    
    return {
        "gmail_query": gmail_query,
        "schedule": schedule,
    }


# ---- API endpoints for user settings -----------------------------------------

@router.get("/settings/gmail-query")
async def get_gmail_query(
    db: aiosqlite.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    """Get the Gmail query, from DB or falling back to config."""
    value = await _get_user_setting_or_default(
        db, SETTING_KEY_GMAIL_QUERY, settings.GMAIL_QUERY, owner_user_id
    )
    return {"gmail_query": value}


@router.post("/settings/gmail-query")
async def set_gmail_query(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    """Set the Gmail query."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    value = data.get("gmail_query", "")
    await queries.set_user_setting(db, SETTING_KEY_GMAIL_QUERY, value, owner_user_id=owner_user_id)
    
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse(
            f'<div id="gmail-query-display" class="text-sm text-foreground mt-0.5 break-all">{value or "-"}</div>'
        )
    return {"gmail_query": value}


@router.get("/settings/schedule")
async def get_schedule(
    db: aiosqlite.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    """Get the schedule, from DB or falling back to config."""
    value = await _get_user_setting_or_default(
        db, SETTING_KEY_SCHEDULE, ",".join(settings.SCHEDULE), owner_user_id
    )
    # Parse comma-separated schedule back to list
    schedule_list = [s.strip() for s in value.split(",") if s.strip()]
    return {"schedule": schedule_list, "schedule_raw": value}


@router.post("/settings/schedule")
async def set_schedule(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    """Set the schedule (comma-separated times like '05:00,10:00,14:00,22:00')."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    value = data.get("schedule", "")
    await queries.set_user_setting(db, SETTING_KEY_SCHEDULE, value, owner_user_id=owner_user_id)
    
    # Parse for display
    schedule_list = [s.strip() for s in value.split(",") if s.strip()]
    display = ", ".join(schedule_list) if schedule_list else "-"
    
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse(
            f'<div id="schedule-display" class="text-sm text-foreground mt-0.5">{display}</div>'
        )
    return {"schedule": schedule_list}


@router.post("/settings/line-test")
async def test_line_message(
    request: Request,
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    """Send a test LINE message."""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN or not settings.LINE_USER_ID:
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(
                '<div id="line-status" class="text-sm text-red-600">LINE not configured.</div>'
            )
        raise HTTPException(status_code=400, detail="LINE not configured")
    
    success = await send_message(
        user_id=settings.LINE_USER_ID,
        text="🔔 Test message from Financial Email Tracker",
        channel_access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
    )
    
    if request.headers.get("HX-Request") == "true":
        if success:
            return HTMLResponse(
                '<div id="line-status" class="text-sm text-green-600">Test message sent!</div>'
            )
        else:
            return HTMLResponse(
                '<div id="line-status" class="text-sm text-red-600">Failed to send test message.</div>'
            )
    
    if success:
        return {"success": True, "message": "Test message sent"}
    raise HTTPException(status_code=500, detail="Failed to send test message")


def _public_url_for(request: Request, route_name: str, settings: Settings) -> str:
    path = request.url_for(route_name).path
    if settings.PUBLIC_BASE_URL:
        return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"
    return str(request.url_for(route_name))


def _public_authorization_response(request: Request, redirect_uri: str) -> str:
    if request.url.query:
        return f"{redirect_uri}?{request.url.query}"
    return redirect_uri


def _masked_gmail_client_id(credentials_path: str) -> str:
    try:
        raw = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "credentials.json not found"

    client_id = (raw.get("web") or raw.get("installed") or {}).get("client_id")
    if not client_id:
        return "client_id not found"
    if len(client_id) <= 20:
        return client_id
    return f"{client_id[:8]}...{client_id[-16:]}"


def _gmail_profile_email(settings: Settings, owner_user_id: int) -> str | None:
    token_path = user_token_path(owner_user_id)
    if not token_exists(token_path):
        return None
    return GmailClient(
        credentials_path=settings.GMAIL_CREDENTIALS_PATH,
        token_path=token_path,
    ).get_profile_email()


@router.get("/settings")
async def get_settings_view(
    request: Request,
    settings: Settings = Depends(get_settings),
    owner_user_id: int = Depends(get_current_user_id),
):
    data = _safe_settings(settings)
    data["gmail_connected"] = token_exists(user_token_path(owner_user_id))
    data["gmail_profile_email"] = _gmail_profile_email(settings, owner_user_id)
    data["gmail_redirect_uri"] = _public_url_for(request, "gmail_oauth_callback", settings)
    data["gmail_oauth_client_id"] = _masked_gmail_client_id(settings.GMAIL_CREDENTIALS_PATH)
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
    connected = token_exists(token_path)
    return {"connected": connected, "profile_email": _gmail_profile_email(settings, owner_user_id)}


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
    authorization_response = _public_authorization_response(request, redirect_uri)
    try:
        exchange_authorization_response(
            redirect_uri=redirect_uri,
            authorization_response=authorization_response,
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
    db: aiosqlite.Connection = Depends(get_db),
):
    # Start with config defaults
    safe_settings = _safe_settings(settings)
    # Override with user settings from database if available
    user_settings = await _load_user_settings(db, settings, owner_user_id)
    safe_settings.update(user_settings)
    # Add Gmail connection status
    safe_settings["gmail_connected"] = token_exists(user_token_path(owner_user_id))
    safe_settings["gmail_profile_email"] = _gmail_profile_email(settings, owner_user_id)
    safe_settings["gmail_redirect_uri"] = _public_url_for(request, "gmail_oauth_callback", settings)
    safe_settings["gmail_oauth_client_id"] = _masked_gmail_client_id(settings.GMAIL_CREDENTIALS_PATH)
    return templates.TemplateResponse(request, "settings.html", {"settings": safe_settings})
