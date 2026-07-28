"""Dashboard route - renders the stats overview page."""

import logging
from datetime import date

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request

from app.config import Settings, get_settings
from app.ingestion.scheduler import next_scheduled_run
from app.storage import queries
from app.web.deps import get_current_user_id, get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


def _delta(today_value: float, yesterday_value: float) -> dict | None:
    """Percent change vs yesterday, or None when there's no baseline to compare against."""
    if not yesterday_value:
        return None
    pct = (today_value - yesterday_value) / yesterday_value * 100
    return {"pct": abs(pct), "up": pct > 0, "flat": abs(pct) < 0.5}


@router.get("/")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
    days: int = Query(7),
    settings: Settings = Depends(get_settings),
):
    days = days if days in (7, 14, 30) else 7
    stats = await queries.get_dashboard_stats(db, owner_user_id=owner_user_id)
    expense_summaries = await queries.get_expense_summary_windows(db, owner_user_id=owner_user_id)
    expense_days = await queries.get_expense_by_day(db, days=days, owner_user_id=owner_user_id)
    max_expense = max((item["total"] for item in expense_days), default=0)
    total_expense = sum(item["total"] for item in expense_days)
    expense_by_bank = await queries.get_expense_by_bank(db, days=days, owner_user_id=owner_user_id)
    pie_segments = queries.build_pie_segments(expense_by_bank)
    runs, _ = await queries.list_runs(db, page=1, page_size=5, owner_user_id=owner_user_id)
    expense_by_category = await queries.get_expense_by_category(db, days=days, owner_user_id=owner_user_id)
    max_category_expense = max((item["total"] for item in expense_by_category), default=0)
    top_counterparties = await queries.get_top_counterparties(db, days=days, owner_user_id=owner_user_id)
    max_counterparty_expense = max((item["total"] for item in top_counterparties), default=0)
    recent_transactions, _ = await queries.list_transactions(db, page=1, page_size=8, owner_user_id=owner_user_id)

    net_today = stats["income_today"] - stats["expense_today"]
    net_yesterday = stats["income_yesterday"] - stats["expense_yesterday"]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "today": date.today().isoformat(),
            "stats": stats,
            "income_delta": _delta(stats["income_today"], stats["income_yesterday"]),
            "expense_delta": _delta(stats["expense_today"], stats["expense_yesterday"]),
            "net_today": net_today,
            "net_delta_amount": net_today - net_yesterday,
            "expense_summaries": expense_summaries,
            "expense_days": expense_days,
            "expense_window_days": days,
            "max_expense": max_expense,
            "total_expense": total_expense,
            "pie_segments": pie_segments,
            "runs": runs,
            "expense_by_category": expense_by_category,
            "max_category_expense": max_category_expense,
            "top_counterparties": top_counterparties,
            "max_counterparty_expense": max_counterparty_expense,
            "recent_transactions": recent_transactions,
            "bank_colors": queries.BANK_COLORS,
            "next_sync": next_scheduled_run(settings),
        },
    )
