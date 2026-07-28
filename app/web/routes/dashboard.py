"""Dashboard route - renders the stats overview page."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request

from app.config import Settings, get_settings
from app.ingestion.scheduler import next_scheduled_run
from app.storage import queries
from app.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    days: int = Query(7),
    settings: Settings = Depends(get_settings),
):
    days = days if days in (7, 14, 30) else 7
    stats = await queries.get_dashboard_stats(db)
    expense_days = await queries.get_expense_by_day(db, days=days)
    max_expense = max((item["total"] for item in expense_days), default=0)
    total_expense = sum(item["total"] for item in expense_days)
    expense_by_bank = await queries.get_expense_by_bank(db, days=days)
    pie_segments = queries.build_pie_segments(expense_by_bank)
    runs, _ = await queries.list_runs(db, page=1, page_size=5)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "expense_days": expense_days,
            "expense_window_days": days,
            "max_expense": max_expense,
            "total_expense": total_expense,
            "pie_segments": pie_segments,
            "runs": runs,
            "next_sync": next_scheduled_run(settings),
        },
    )
