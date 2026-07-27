"""Shared FastAPI dependencies for the web routes."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.classification.engine import CategoryEngine
from app.config import Settings, get_settings
from app.gmail.client import GmailClient
from app.parsers.registry import ParserRegistry
from app.storage.database import get_connection

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


async def get_db():
    db = await get_connection()
    try:
        yield db
    finally:
        await db.close()


def get_category_engine(settings: Settings | None = None) -> CategoryEngine:
    settings = settings or get_settings()
    return CategoryEngine(
        ai_enabled=settings.AI_ENABLED,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_MODEL,
    )


def get_gmail_client() -> GmailClient:
    return GmailClient()


def get_parser_registry() -> ParserRegistry:
    return ParserRegistry()
