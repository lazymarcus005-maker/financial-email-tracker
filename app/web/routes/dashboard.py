"""Dashboard route - renders the stats overview page."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request

from app.storage import queries
from app.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    days: int = Query(7),
):
    days = days if days in (7, 14, 30) else 7
    stats = await queries.get_dashboard_stats(db)
    expense_summaries = await queries.get_expense_summary_windows(db)
    expense_days = await queries.get_expense_by_day(db, days=days)
    max_expense = max((item["total"] for item in expense_days), default=0)
    runs, _ = await queries.list_runs(db, page=1, page_size=5)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "expense_summaries": expense_summaries,
            "expense_days": expense_days,
            "expense_window_days": days,
            "max_expense": max_expense,
            "runs": runs,
        },
    )
