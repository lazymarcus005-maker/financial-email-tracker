"""Ingestion routes - run history, trigger a run now, retry failed messages."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.classification.engine import CategoryEngine
from app.config import Settings, get_settings
from app.gmail.client import GmailClient
from app.ingestion.reparse import reparse_unknown
from app.ingestion.service import IngestionAlreadyRunningError, run_ingestion
from app.parsers.registry import ParserRegistry
from app.storage import queries
from app.web.deps import get_category_engine, get_db, get_gmail_client, get_parser_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.get("/runs")
async def list_runs(
    db: aiosqlite.Connection = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total = await queries.list_runs(db, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/ingestion/run")
async def trigger_run(
    request: Request,
    settings: Settings = Depends(get_settings),
    engine: CategoryEngine = Depends(get_category_engine),
):
    try:
        summary = await run_ingestion(settings.GMAIL_QUERY, engine=engine)
    except IngestionAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    if request.headers.get("hx-request") == "true":
        new_count = summary.get("new", 0)
        scanned_count = summary.get("scanned", 0)
        return HTMLResponse(
            f"""<div id="ingestion-run">
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
        <span class="button-text">Done: {new_count} new, {scanned_count} scanned</span>
    </button>
</div>"""
        )

    return summary


@router.post("/ingestion/retry/{run_id}")
async def retry_run(
    run_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    registry: ParserRegistry = Depends(get_parser_registry),
    engine: CategoryEngine = Depends(get_category_engine),
):
    """Retry all currently-pending unknown patterns (best-effort - runs aren't tracked per-message)."""
    run = await queries.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    pending, _ = await queries.list_unknown(db, page=1, page_size=queries.MAX_PAGE_SIZE, status="pending")

    parsed = failed = 0
    for item in pending:
        result = await reparse_unknown(db, item["id"], gmail_client=gmail_client, registry=registry, engine=engine)
        if result["status"] == "parsed":
            parsed += 1
        else:
            failed += 1

    return {"run_id": run_id, "retried": len(pending), "parsed": parsed, "failed": failed}
