"""Settings route - read-only view of non-secret configuration."""

import logging
import json

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import Settings, get_settings
from app.storage import queries
from app.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])
page_router = APIRouter(tags=["settings-pages"])


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
async def get_settings_view(settings: Settings = Depends(get_settings)):
    return _safe_settings(settings)


@router.post("/settings/clear-data")
async def clear_all_data(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    counts = await queries.clear_runtime_data(db)
    if request.headers.get("HX-Request") == "true":
        deleted = sum(counts.values())
        return HTMLResponse(
            f'<div id="settings-data-status" class="text-sm text-green-600">Cleared {deleted} rows.</div>'
        )
    return {"cleared": counts}


@router.get("/settings/export")
async def export_data(db: aiosqlite.Connection = Depends(get_db)):
    payload = await queries.export_runtime_data(db)
    return JSONResponse(
        payload,
        headers={"Content-Disposition": 'attachment; filename="financial-email-tracker-export.json"'},
    )


@router.post("/settings/import")
async def import_data(
    request: Request,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    try:
        raw = await file.read()
        payload = json.loads(raw.decode("utf-8"))
        imported = await queries.import_runtime_data(db, payload, replace=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if request.headers.get("HX-Request") == "true":
        total = sum(imported.values())
        return HTMLResponse(
            f'<div id="settings-data-status" class="text-sm text-green-600">Imported {total} rows.</div>'
        )
    return {"imported": imported}


@page_router.get("/settings")
async def settings_page(request: Request, settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse(request, "settings.html", {"settings": _safe_settings(settings)})
