"""Dashboard route - renders the stats overview page."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.storage import queries
from app.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    stats = await queries.get_dashboard_stats(db)
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats})
