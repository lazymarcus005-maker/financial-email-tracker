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
from app.web.deps import (
    get_category_engine,
    get_current_user_id,
    get_db,
    get_gmail_client,
    get_optional_gmail_client,
    get_parser_registry,
    templates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["transactions"])
page_router = APIRouter(tags=["transactions-pages"])


class TransactionUpdate(BaseModel):
    category: str | None = None
    ignore: bool | None = None


@router.get("/transactions")
async def list_transactions(
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    transaction_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
):
    items, total = await queries.list_transactions(
        db,
        page=page,
        page_size=page_size,
        owner_user_id=owner_user_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        transaction_type=transaction_type,
        direction=direction,
        search=search,
        sort=sort,
        sort_dir=dir,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/transactions/{transaction_id}")
async def get_transaction(
    transaction_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.patch("/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Accept both JSON and form-encoded data (for HTMX inline editing)
    ct = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in ct:
        form = await request.form()
        category = form.get("category")
        ignore_raw = form.get("ignore")
        if ignore_raw is not None:
            await queries.set_transaction_ignored(db, transaction_id, ignore_raw == "true", owner_user_id=owner_user_id)
    else:
        body = await request.json()
        category = body.get("category")
        ignore = body.get("ignore")
        if ignore is not None:
            await queries.set_transaction_ignored(db, transaction_id, ignore, owner_user_id=owner_user_id)

    if category is not None:
        await queries.update_transaction_category(
            db, transaction_id, category, category_source="manual", owner_user_id=owner_user_id
        )
        await history.record(
            db, transaction["counterparty"], category, source="manual", owner_user_id=owner_user_id
        )
        await db.commit()

    t = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    # HTMX: return HTML partial
    if request.headers.get("HX-Request") == "true":
        if request.headers.get("HX-Target") == "transaction-actions":
            return templates.TemplateResponse(request, "partials/transaction_actions.html", {"t": t})
        return templates.TemplateResponse(request, "partials/category_badge.html", {"t": t})
    return t


@router.delete("/transactions/{transaction_id}", status_code=204)
async def delete_transaction(
    transaction_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    await queries.delete_transaction(db, transaction_id, owner_user_id=owner_user_id)
    # HTMX: redirect to transactions list
    if request.headers.get("HX-Request") == "true":
        from fastapi.responses import HTMLResponse
        return HTMLResponse('<script>window.location.href="/transactions"</script>')
    return None


@router.post("/reparse/{transaction_id}")
async def reparse(
    transaction_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    registry: ParserRegistry = Depends(get_parser_registry),
    engine: CategoryEngine = Depends(get_category_engine),
    owner_user_id: int = Depends(get_current_user_id),
):
    result = await reparse_transaction(
        db,
        transaction_id,
        gmail_client=gmail_client,
        registry=registry,
        engine=engine,
        owner_user_id=owner_user_id,
    )
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    # HTMX: return updated actions partial
    if request.headers.get("HX-Request") == "true":
        t = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
        return templates.TemplateResponse(request, "partials/transaction_actions.html", {"t": t})
    return result


@router.get("/transactions/{transaction_id}/raw-email")
async def get_transaction_raw_email(
    transaction_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient | None = Depends(get_optional_gmail_client),
    owner_user_id: int = Depends(get_current_user_id),
):
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if gmail_client is None:
        email = None
        error = "Connect Gmail in Settings before viewing the original email."
    else:
        try:
            message = gmail_client.get_message(transaction["gmail_message_id"])
            email = {"sender": message.sender, "subject": message.subject, "received_at": message.received_at, "body_text": message.body_text}
            error = None
        except Exception as e:
            logger.warning(f"Failed to fetch raw email for transaction {transaction_id}: {e}")
            email = None
            error = "Could not load the original email. It may have been deleted, or Gmail access failed."
    return templates.TemplateResponse(request, "partials/raw_email.html", {"email": email, "error": error})


@page_router.get("/transactions/{transaction_id}/modal")
async def transaction_detail_modal(
    request: Request,
    transaction_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    categories = await queries.list_categories(db, owner_user_id=owner_user_id)
    return templates.TemplateResponse(request, "partials/transaction_detail_modal.html", {"t": transaction, "categories": categories})


@page_router.get("/transactions/{transaction_id}/edit-category")
async def edit_category_fragment(
    request: Request,
    transaction_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    """HTMX fragment: inline category editor with datalist."""
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    categories = await queries.list_categories(db, owner_user_id=owner_user_id)
    return templates.TemplateResponse(
        request,
        "fragments/edit_category.html",
        {"t": transaction, "categories": categories},
    )


@page_router.get("/transactions")
async def transactions_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    transaction_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
):
    items, total = await queries.list_transactions(
        db,
        page=page,
        page_size=page_size,
        owner_user_id=owner_user_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        transaction_type=transaction_type,
        direction=direction,
        search=search,
        sort=sort,
        sort_dir=dir,
    )
    total_pages = max(1, -(-total // page_size))
    categories = await queries.list_categories(db, owner_user_id=owner_user_id)
    types = await queries.list_transaction_types(db, owner_user_id=owner_user_id)
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
            "sort": sort or "",
            "sort_dir": dir or "",
            "categories": categories,
            "types": types,
        },
    )


@page_router.get("/transactions/{transaction_id}")
async def transaction_detail_page(
    request: Request,
    transaction_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    transaction = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    categories = await queries.list_categories(db, owner_user_id=owner_user_id)
    return templates.TemplateResponse(request, "transaction_detail.html", {"t": transaction, "categories": categories})


