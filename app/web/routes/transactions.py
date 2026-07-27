"""Transaction routes - list, detail, edit category / ignore, reparse."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.classification import history
from app.classification.engine import CategoryEngine
from app.gmail.client import GmailClient
from app.ingestion.reparse import reparse_transaction
from app.parsers.registry import ParserRegistry
from app.storage import queries
from app.web.deps import get_category_engine, get_db, get_gmail_client, get_parser_registry, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["transactions"])
page_router = APIRouter(tags=["transactions-pages"])


class TransactionUpdate(BaseModel):
    category: str | None = None
    ignore: bool | None = None


@router.get("/transactions")
async def list_transactions(
    db: aiosqlite.Connection = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    transaction_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
):
    items, total = await queries.list_transactions(
        db,
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
        category=category,
        transaction_type=transaction_type,
        direction=direction,
        search=search,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: int, db: aiosqlite.Connection = Depends(get_db)):
    transaction = await queries.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.patch("/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: int,
    body: TransactionUpdate,
    db: aiosqlite.Connection = Depends(get_db),
):
    transaction = await queries.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if body.category is not None:
        await queries.update_transaction_category(db, transaction_id, body.category, category_source="manual")
        await history.record(db, transaction["counterparty"], body.category, source="manual")
        await db.commit()

    if body.ignore is not None:
        await queries.set_transaction_ignored(db, transaction_id, body.ignore)

    return await queries.get_transaction(db, transaction_id)


@router.delete("/transactions/{transaction_id}", status_code=204)
async def delete_transaction(
    transaction_id: int,
    db: aiosqlite.Connection = Depends(get_db),
):
    transaction = await queries.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    await queries.delete_transaction(db, transaction_id)
    return None


@router.post("/reparse/{transaction_id}")
async def reparse(
    transaction_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    registry: ParserRegistry = Depends(get_parser_registry),
    engine: CategoryEngine = Depends(get_category_engine),
):
    result = await reparse_transaction(db, transaction_id, gmail_client=gmail_client, registry=registry, engine=engine)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


@page_router.get("/transactions")
async def transactions_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    transaction_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
):
    items, total = await queries.list_transactions(
        db,
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
        category=category,
        transaction_type=transaction_type,
        direction=direction,
        search=search,
    )
    total_pages = max(1, -(-total // page_size))
    return templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters": {
                "date_from": date_from or "",
                "date_to": date_to or "",
                "category": category or "",
                "transaction_type": transaction_type or "",
                "direction": direction or "",
                "search": search or "",
            },
        },
    )


@page_router.get("/transactions/{transaction_id}")
async def transaction_detail_page(request: Request, transaction_id: int, db: aiosqlite.Connection = Depends(get_db)):
    transaction = await queries.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return templates.TemplateResponse(request, "transaction_detail.html", {"t": transaction})
