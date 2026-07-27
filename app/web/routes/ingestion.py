"""Ingestion routes - run history, trigger a run now, retry failed messages."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

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
    settings: Settings = Depends(get_settings),
    engine: CategoryEngine = Depends(get_category_engine),
):
    try:
        summary = await run_ingestion(settings.GMAIL_QUERY, engine=engine)
    except IngestionAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
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
