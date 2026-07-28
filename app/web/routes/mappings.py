"""Counterparty mapping routes - CRUD for counterparty -> category rules."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.storage import queries
from app.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mappings"])
page_router = APIRouter(tags=["mappings-pages"])


class MappingCreate(BaseModel):
    counterparty: str
    category: str


class MappingUpdate(BaseModel):
    category: str


async def _mapping_payload(request: Request) -> dict:
    ct = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
        form = await request.form()
        return dict(form)
    return await request.json()


@router.get("/mappings")
async def list_mappings(db: aiosqlite.Connection = Depends(get_db)):
    return {"items": await queries.list_mappings(db)}


@router.post("/mappings", status_code=201)
async def create_mapping(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    body = MappingCreate(**await _mapping_payload(request))
    item = await queries.create_mapping(db, body.counterparty, body.category, source="manual")
    # HTMX: return new row partial
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/mapping_row.html", {"item": item})
    return item


@router.patch("/mappings/{mapping_id}")
async def update_mapping(mapping_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    existing = await queries.get_mapping(db, mapping_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    body = MappingUpdate(**await _mapping_payload(request))
    await queries.update_mapping(db, mapping_id, body.category)
    item = await queries.get_mapping(db, mapping_id)
    # HTMX: return updated row partial
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/mapping_row.html", {"item": item})
    return item


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    existing = await queries.get_mapping(db, mapping_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    await queries.delete_mapping(db, mapping_id)
    # HTMX: return empty to remove row
    if request.headers.get("HX-Request") == "true":
        from fastapi.responses import HTMLResponse
        return HTMLResponse("")
    return None


@page_router.get("/mappings")
async def mappings_page(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    items = await queries.list_mappings(db)
    counterparty_options = await queries.list_counterparty_options(db)
    category_options = await queries.list_category_options(db)
    return templates.TemplateResponse(
        request,
        "mappings.html",
        {
            "items": items,
            "counterparty_options": counterparty_options,
            "category_options": category_options,
        },
    )
