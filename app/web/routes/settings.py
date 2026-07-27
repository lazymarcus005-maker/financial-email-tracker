"""Settings route - read-only view of non-secret configuration."""

import logging

from fastapi import APIRouter, Depends, Request

from app.config import Settings, get_settings
from app.web.deps import templates

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


@page_router.get("/settings")
async def settings_page(request: Request, settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse(request, "settings.html", {"settings": _safe_settings(settings)})
