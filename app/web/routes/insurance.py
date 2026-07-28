"""Insurance routes - CRUD for policy records with optional logo URLs."""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.storage import queries
from app.web.deps import get_current_user_id, get_db, templates

router = APIRouter(prefix="/api", tags=["insurance"])
page_router = APIRouter(tags=["insurance-pages"])

INSURANCE_TYPES = ["life", "health", "car", "travel", "property", "accident", "other"]
INSURANCE_STATUSES = ["active", "pending", "expired", "cancelled"]
PREMIUM_FREQUENCIES = ["monthly", "quarterly", "semiannual", "annual", "one-time", "other"]


def _text(value) -> str:
    return str(value or "").strip()


def _optional_text(value) -> str | None:
    text = _text(value)
    return text or None


def _optional_float(value) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid number: {value}") from exc


async def _payload(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return dict(form)
    return await request.json()


def _normalize_body(body: dict) -> dict:
    insurer_name = _text(body.get("insurer_name"))
    policy_name = _text(body.get("policy_name"))
    if not insurer_name:
        raise HTTPException(status_code=400, detail="Insurer name is required")
    if not policy_name:
        raise HTTPException(status_code=400, detail="Policy name is required")

    policy_type = _text(body.get("policy_type")).lower() or "other"
    if policy_type not in INSURANCE_TYPES:
        policy_type = "other"

    status = _text(body.get("status")).lower() or "active"
    if status not in INSURANCE_STATUSES:
        status = "active"

    premium_frequency = _text(body.get("premium_frequency")).lower() or "annual"
    if premium_frequency not in PREMIUM_FREQUENCIES:
        premium_frequency = "other"

    return {
        "insurer_name": insurer_name,
        "policy_name": policy_name,
        "policy_number": _optional_text(body.get("policy_number")),
        "policy_type": policy_type,
        "insured_person": _optional_text(body.get("insured_person")),
        "logo_url": _optional_text(body.get("logo_url")),
        "premium_amount": _optional_float(body.get("premium_amount")),
        "premium_frequency": premium_frequency,
        "coverage_amount": _optional_float(body.get("coverage_amount")),
        "start_date": _optional_text(body.get("start_date")),
        "end_date": _optional_text(body.get("end_date")),
        "renewal_date": _optional_text(body.get("renewal_date")),
        "status": status,
        "contact_phone": _optional_text(body.get("contact_phone")),
        "contact_email": _optional_text(body.get("contact_email")),
        "notes": _optional_text(body.get("notes")),
    }


async def _insurance_cards(request: Request, items: list[dict]):
    return templates.TemplateResponse(
        request,
        "partials/insurance_cards.html",
        {
            "items": items,
            "insurance_types": INSURANCE_TYPES,
            "insurance_statuses": INSURANCE_STATUSES,
            "premium_frequencies": PREMIUM_FREQUENCIES,
        },
    )


@router.get("/insurance")
async def list_insurance(
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    return {"items": await queries.list_insurance_policies(db, owner_user_id=owner_user_id)}


@router.post("/insurance", status_code=201)
async def create_insurance(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    body = _normalize_body(await _payload(request))
    await queries.create_insurance_policy(db, owner_user_id=owner_user_id, **body)
    items = await queries.list_insurance_policies(db, owner_user_id=owner_user_id)
    if request.headers.get("HX-Request") == "true":
        return await _insurance_cards(request, items)
    return {"items": items}


@router.patch("/insurance/{insurance_id}")
async def update_insurance(
    insurance_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    existing = await queries.get_insurance_policy(db, insurance_id, owner_user_id=owner_user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Insurance policy not found")
    body = _normalize_body({**existing, **(await _payload(request))})
    await queries.update_insurance_policy(db, insurance_id, owner_user_id=owner_user_id, **body)
    items = await queries.list_insurance_policies(db, owner_user_id=owner_user_id)
    if request.headers.get("HX-Request") == "true":
        return await _insurance_cards(request, items)
    return {"items": items}


@router.delete("/insurance/{insurance_id}", status_code=204)
async def delete_insurance(
    insurance_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    existing = await queries.get_insurance_policy(db, insurance_id, owner_user_id=owner_user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Insurance policy not found")
    await queries.delete_insurance_policy(db, insurance_id, owner_user_id=owner_user_id)
    if request.headers.get("HX-Request") == "true":
        items = await queries.list_insurance_policies(db, owner_user_id=owner_user_id)
        return await _insurance_cards(request, items)
    return None


@page_router.get("/insurance")
async def insurance_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    owner_user_id: int = Depends(get_current_user_id),
):
    items = await queries.list_insurance_policies(db, owner_user_id=owner_user_id)
    return templates.TemplateResponse(
        request,
        "insurance.html",
        {
            "items": items,
            "insurance_types": INSURANCE_TYPES,
            "insurance_statuses": INSURANCE_STATUSES,
            "premium_frequencies": PREMIUM_FREQUENCIES,
        },
    )
