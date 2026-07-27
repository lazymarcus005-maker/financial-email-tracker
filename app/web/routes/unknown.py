"""Unknown pattern routes - list unparseable emails, ignore or reparse them."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.classification.engine import CategoryEngine
from app.gmail.client import GmailClient
from app.ingestion.reparse import reparse_unknown
from app.parsers.registry import ParserRegistry
from app.storage import queries
from app.web.deps import get_category_engine, get_db, get_gmail_client, get_parser_registry, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["unknown"])
page_router = APIRouter(tags=["unknown-pages"])


@router.get("/unknown")
async def list_unknown(
    db: aiosqlite.Connection = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    items, total = await queries.list_unknown(db, page=page, page_size=page_size, status=status)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/unknown/{unknown_id}/ignore")
async def ignore_unknown(unknown_id: int, db: aiosqlite.Connection = Depends(get_db)):
    row = await queries.get_unknown(db, unknown_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    await queries.set_unknown_status(db, unknown_id, "ignored")
    return await queries.get_unknown(db, unknown_id)


@router.delete("/unknown/{unknown_id}", status_code=204)
async def delete_unknown(unknown_id: int, db: aiosqlite.Connection = Depends(get_db)):
    row = await queries.get_unknown(db, unknown_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    await queries.delete_unknown(db, unknown_id)
    return None


@router.post("/unknown/{unknown_id}/reparse")
async def reparse(
    unknown_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    registry: ParserRegistry = Depends(get_parser_registry),
    engine: CategoryEngine = Depends(get_category_engine),
):
    result = await reparse_unknown(db, unknown_id, gmail_client=gmail_client, registry=registry, engine=engine)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    return result


@page_router.get("/unknown")
async def unknown_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    items, total = await queries.list_unknown(db, page=page, page_size=page_size, status=status)
    total_pages = max(1, -(-total // page_size))
    return templates.TemplateResponse(
        request,
        "unknown.html",
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters": {"status": status or ""},
        },
    )
