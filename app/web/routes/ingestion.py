"""Ingestion routes - run history, trigger a run now, retry failed messages."""

import html
import logging
import re

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.classification.engine import CategoryEngine
from app.config import get_settings
from app.gmail.client import GmailClient
from app.gmail.reader import GmailReader
from app.ingestion.reparse import reparse_unknown
from app.ingestion.service import IngestionAlreadyRunningError, run_ingestion
from app.integrations.line import format_daily_summary, send_message
from app.parsers.registry import ParserRegistry
from app.storage import queries
from app.web.deps import get_category_engine, get_current_user_id, get_db, get_gmail_client, get_parser_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ingestion"])

_INGESTION_WINDOWS = {
    "default": None,
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 90,
}


async def _get_ingestion_window(request: Request) -> str:
    ct = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
        form = await request.form()
        window = str(form.get("window") or "default")
    else:
        window = request.query_params.get("window", "default")
    return window if window in _INGESTION_WINDOWS else "default"


def _query_for_window(base_query: str, window: str) -> str:
    days = _INGESTION_WINDOWS[window]
    if days is None:
        return base_query
    query = re.sub(r"\s*newer_than:\S+", "", base_query).strip()
    return f"{query} newer_than:{days}d"


def _ingestion_control_html(button_text: str, selected_window: str = "default", status_text: str | None = None) -> str:
    options = {
        "default": "Configured",
        "last_7_days": "Last 7 days",
        "last_30_days": "Last 30 days",
        "last_90_days": "Last 90 days",
    }
    option_html = "\n".join(
        f'<option value="{value}" {"selected" if value == selected_window else ""}>{label}</option>'
        for value, label in options.items()
    )
    status_html = ""
    if status_text:
        status_html = f'<div class="basis-full text-xs text-neutral-500">{html.escape(status_text)}</div>'
    return f"""<form id="ingestion-run" class="flex flex-wrap items-center gap-2">
    <select name="window" class="input w-32">
        {option_html}
    </select>
    <button
        class="px-4 py-2 rounded-lg bg-neutral-900 text-white text-sm font-medium hover:bg-neutral-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
        hx-post="/api/ingestion/run"
        hx-swap="outerHTML"
        hx-target="#ingestion-run"
        hx-disabled-elt="this"
    >
        <span class="spinner hidden htmx-indicator">
            <svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
        </span>
        <span class="button-text">{html.escape(button_text)}</span>
    </button>
    {status_html}
</form>"""


def _summary_status_text(summary: dict, window: str) -> str:
    scanned = summary.get("emails_checked", 0)
    inserted = summary.get("inserted", 0)
    duplicates = summary.get("duplicates", 0)
    failed = summary.get("failed", 0)
    if scanned == 0:
        return f"0 scanned for {window}; Gmail search found no matching email."
    return f"{scanned} scanned, {inserted} saved, {duplicates} duplicate, {failed} failed."


def _line_summary_button_html(label: str = "Send LINE Summary", status_text: str | None = None) -> str:
    status_html = ""
    if status_text:
        status_html = f'<span class="text-xs text-muted-foreground">{html.escape(status_text)}</span>'
    return f"""<div id="line-summary-btn" class="flex items-center gap-2">
    <button
        class="btn btn-secondary btn-md"
        hx-post="/api/line/summary"
        hx-swap="outerHTML"
        hx-target="#line-summary-btn"
        hx-disabled-elt="this"
    >
        <span class="htmx-indicator">
            <svg class="h-4 w-4 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
        </span>
        <span>{html.escape(label)}</span>
    </button>
    {status_html}
</div>"""


@router.post("/line/summary")
async def send_line_summary(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    settings = get_settings()
    data = await queries.get_daily_summary_data(db, owner_user_id=owner_user_id)
    text = format_daily_summary(data)
    sent = await send_message(settings.LINE_USER_ID, text, settings.LINE_CHANNEL_ACCESS_TOKEN)

    if request.headers.get("hx-request") == "true":
        if sent:
            return HTMLResponse(_line_summary_button_html("Sent!", status_text="✅ Summary sent to LINE"))
        return HTMLResponse(_line_summary_button_html("Send LINE Summary", status_text="❌ Failed — check LINE config"))

    return {"sent": sent}


@router.get("/runs")
async def list_runs(
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total = await queries.list_runs(db, page=page, page_size=page_size, owner_user_id=owner_user_id)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/ingestion/run")
async def trigger_run(
    request: Request,
    engine: CategoryEngine = Depends(get_category_engine),
    gmail_client: GmailClient = Depends(get_gmail_client),
    owner_user_id: int = Depends(get_current_user_id),
):
    settings = get_settings()
    window = await _get_ingestion_window(request)
    query = _query_for_window(settings.GMAIL_QUERY, window)
    reader = GmailReader(gmail_client)
    try:
        summary = await run_ingestion(query, reader=reader, engine=engine, owner_user_id=owner_user_id)
    except IngestionAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    if request.headers.get("hx-request") == "true":
        status_text = _summary_status_text(summary, window)
        button_text = (
            f"Done: {summary.get('emails_checked', 0)} scanned, "
            f"{summary.get('inserted', 0)} new"
        )
        return HTMLResponse(_ingestion_control_html(button_text, selected_window=window, status_text=status_text))

    return summary


@router.post("/ingestion/retry/{run_id}")
async def retry_run(
    run_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    registry: ParserRegistry = Depends(get_parser_registry),
    engine: CategoryEngine = Depends(get_category_engine),
    owner_user_id: int = Depends(get_current_user_id),
):
    """Retry all currently-pending unknown patterns (best-effort - runs aren't tracked per-message)."""
    run = await queries.get_run(db, run_id, owner_user_id=owner_user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    pending, _ = await queries.list_unknown(
        db, page=1, page_size=queries.MAX_PAGE_SIZE, status="pending", owner_user_id=owner_user_id
    )

    parsed = failed = 0
    for item in pending:
        result = await reparse_unknown(
            db,
            item["id"],
            gmail_client=gmail_client,
            registry=registry,
            engine=engine,
            owner_user_id=owner_user_id,
        )
        if result["status"] == "parsed":
            parsed += 1
        else:
            failed += 1

    return {"run_id": run_id, "retried": len(pending), "parsed": parsed, "failed": failed}
