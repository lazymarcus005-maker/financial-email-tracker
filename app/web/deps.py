"""Shared FastAPI dependencies for the web routes."""

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.classification.engine import CategoryEngine
from app.config import get_settings
from app.gmail.authorize import token_exists, user_token_path
from app.gmail.client import GmailClient
from app.parsers.registry import ParserRegistry
from app.storage.database import get_connection

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def _thai_date_format(value: str | None, fmt: str = "short") -> str:
    """Format a date string (ISO or SQL) into Thai-locale display.

    fmt='short' -> '27 ก.ค. 2569'
    fmt='long'  -> '27 กรกฎาคม 2569'
    fmt='full'  -> 'วันจันทร์ที่ 27 กรกฎาคม 2569'
    """
    if not value:
        return ""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("T", " "))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return str(value)

    year_be = dt.year + 543  # Buddhist era
    month_name = _THAI_MONTHS[dt.month - 1]

    if fmt == "long":
        return f"{dt.day} {month_name} {year_be}"
    elif fmt == "full":
        thai_days = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]
        return f"{thai_days[dt.weekday()]}ที่ {dt.day} {month_name} {year_be}"
    else:
        # short: "27 ก.ค. 2569"
        short_months = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
        return f"{dt.day} {short_months[dt.month - 1]} {year_be}"


templates.env.filters["thai_date"] = _thai_date_format


async def get_db():
    db = await get_connection()
    try:
        yield db
    finally:
        await db.close()


def get_current_user(request: Request) -> dict:
    return request.state.current_user


def get_current_user_id(request: Request) -> int:
    return request.state.current_user["id"]


def get_category_engine() -> CategoryEngine:
    settings = get_settings()
    return CategoryEngine(
        ai_enabled=settings.AI_ENABLED,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_MODEL,
    )


def get_gmail_client(request: Request) -> GmailClient:
    user_id = request.state.current_user["id"]
    settings = get_settings()
    token_path = user_token_path(user_id)
    if not token_exists(token_path):
        raise HTTPException(status_code=400, detail="Connect Gmail in Settings before running this action")
    return GmailClient(credentials_path=settings.GMAIL_CREDENTIALS_PATH, token_path=token_path)


def get_parser_registry() -> ParserRegistry:
    return ParserRegistry()
