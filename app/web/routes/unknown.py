"""Unknown pattern routes - list unparseable emails, ignore or reparse them."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from app.classification.engine import CategoryEngine
from app.gmail.client import GmailClient
from app.ingestion import persistence
from app.ingestion.reparse import reparse_unknown
from app.parsers.registry import ParserRegistry
from app.storage import queries
from app.web.deps import (
    get_category_engine,
    get_current_user_id,
    get_db,
    get_gmail_client,
    get_parser_registry,
    templates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["unknown"])
page_router = APIRouter(tags=["unknown-pages"])


@router.get("/unknown")
async def list_unknown(
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    items, total = await queries.list_unknown(
        db, page=page, page_size=page_size, status=status, owner_user_id=owner_user_id
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/unknown/{unknown_id}/ignore")
async def ignore_unknown(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    row = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    await queries.set_unknown_status(db, unknown_id, "ignored", owner_user_id=owner_user_id)
    item = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    # HTMX: return updated row partial
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/unknown_row.html", {"item": item})
    return item


@router.post("/unknown/{unknown_id}/ignore-subject")
async def ignore_unknown_subject(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    row = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    subject = row.get("subject")
    if not subject:
        raise HTTPException(status_code=400, detail="Unknown email has no subject")
    await queries.create_ignored_subject(db, subject, reason="unknown email", owner_user_id=owner_user_id)
    await queries.mark_unknown_subject_ignored(db, subject, owner_user_id=owner_user_id)
    item = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/unknown_row.html", {"item": item})
    return item


@router.delete("/ignored-subjects/{ignored_subject_id}", status_code=204)
async def delete_ignored_subject(
    ignored_subject_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    await queries.delete_ignored_subject(db, ignored_subject_id, owner_user_id=owner_user_id)
    if request.headers.get("HX-Request") == "true":
        from fastapi.responses import HTMLResponse
        return HTMLResponse("")
    return None


@router.delete("/unknown/{unknown_id}", status_code=204)
async def delete_unknown(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    row = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    await queries.delete_unknown(db, unknown_id, owner_user_id=owner_user_id)
    # HTMX: return empty to remove row
    if request.headers.get("HX-Request") == "true":
        from fastapi.responses import HTMLResponse
        return HTMLResponse("")
    return None


@router.post("/unknown/{unknown_id}/reparse")
async def reparse(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    registry: ParserRegistry = Depends(get_parser_registry),
    engine: CategoryEngine = Depends(get_category_engine),
    owner_user_id: int = Depends(get_current_user_id),
):
    result = await reparse_unknown(
        db,
        unknown_id,
        gmail_client=gmail_client,
        registry=registry,
        engine=engine,
        owner_user_id=owner_user_id,
    )
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    # HTMX: return updated row partial
    if request.headers.get("HX-Request") == "true":
        item = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
        return templates.TemplateResponse(request, "partials/unknown_row.html", {"item": item})
    return result


@router.get("/unknown/{unknown_id}/raw-email")
async def get_unknown_raw_email(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    gmail_client: GmailClient = Depends(get_gmail_client),
    owner_user_id: int = Depends(get_current_user_id),
):
    row = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    try:
        message = gmail_client.get_message(row["gmail_message_id"])
        email = {"sender": message.sender, "subject": message.subject, "received_at": message.received_at, "body_text": message.body_text}
        error = None
    except Exception as e:
        logger.warning(f"Failed to fetch raw email for unknown pattern {unknown_id}: {e}")
        email = None
        error = "Could not load the original email. It may have been deleted, or Gmail access failed."
    return templates.TemplateResponse(request, "partials/raw_email.html", {"email": email, "error": error})


@router.post("/unknown/{unknown_id}/promote")
async def promote_unknown(
    unknown_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    registry: ParserRegistry = Depends(get_parser_registry),
    owner_user_id: int = Depends(get_current_user_id),
    transaction_type: str = Form(...),
    direction: str = Form(...),
    status: str = Form(...),
    occurred_at: str = Form(...),
    amount: float = Form(...),
    category: str = Form(...),
    fee: float = Form(0.0),
    available_balance: float | None = Form(None),
    counterparty: str | None = Form(None),
    description: str | None = Form(None),
):
    row = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Cannot promote a {row['status']} record")

    bank = registry.identify_bank(row["sender"]) if row["sender"] else None
    transaction_id = await persistence.insert_manual_transaction(
        db,
        gmail_message_id=row["gmail_message_id"],
        bank=bank,
        transaction_type=transaction_type,
        direction=direction,
        status=status,
        occurred_at=occurred_at,
        amount=amount,
        category=category,
        fee=fee,
        available_balance=available_balance,
        counterparty=counterparty,
        description=description,
        owner_user_id=owner_user_id,
    )
    await persistence.resolve_unknown(db, unknown_id, transaction_id, owner_user_id=owner_user_id)
    await db.commit()

    item = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    return templates.TemplateResponse(request, "partials/unknown_promoted.html", {"item": item})


@page_router.get("/unknown/{unknown_id}/modal")
async def unknown_detail_modal(
    request: Request,
    unknown_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    item = await queries.get_unknown(db, unknown_id, owner_user_id=owner_user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown pattern not found")
    categories = await queries.list_categories(db, owner_user_id=owner_user_id)
    types = await queries.list_transaction_types(db, owner_user_id=owner_user_id)
    return templates.TemplateResponse(
        request, "partials/unknown_detail_modal.html", {"item": item, "categories": categories, "types": types}
    )


@page_router.get("/unknown")
async def unknown_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    items, total = await queries.list_unknown(
        db, page=page, page_size=page_size, status=status, owner_user_id=owner_user_id
    )
    ignored_subjects = await queries.list_ignored_subjects(db, owner_user_id=owner_user_id)
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
            "ignored_subjects": ignored_subjects,
        },
    )
