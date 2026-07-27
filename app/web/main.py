"""Main web app - Routes and middleware."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.ingestion.scheduler import start_scheduler
from app.logging_config import configure_logging
from app.storage.database import init_db
from app.web.routes import dashboard, ingestion, mappings, settings, transactions, unknown

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_settings = get_settings()
    configure_logging(level=app_settings.LOG_LEVEL, fmt=app_settings.LOG_FORMAT)
    await init_db()
    scheduler = start_scheduler()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Financial Email Tracker",
    description="Track financial transactions from Gmail",
    version="2.0.0",
    lifespan=lifespan,
)

# Serve static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(dashboard.router)
app.include_router(transactions.router)
app.include_router(transactions.page_router)
app.include_router(unknown.router)
app.include_router(unknown.page_router)
app.include_router(mappings.router)
app.include_router(mappings.page_router)
app.include_router(ingestion.router)
app.include_router(settings.router)
app.include_router(settings.page_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
