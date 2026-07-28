"""Main web app - Routes and middleware."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.ingestion.scheduler import start_scheduler
from app.logging_config import configure_logging
from app.storage import queries
from app.storage.database import get_connection, init_db
from app.web.auth import is_public_path, load_user_from_request, unauthenticated_response
from app.web.routes import auth, dashboard, ingestion, mappings, settings, transactions, unknown, users

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

@app.middleware("http")
async def auth_middleware(request, call_next):
    request.state.current_user = await load_user_from_request(request)
    if is_public_path(request.url.path):
        return await call_next(request)

    db = await get_connection()
    try:
        has_users = await queries.count_users(db) > 0
    finally:
        await db.close()

    if not has_users:
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "Setup required"}, status_code=409)
        return RedirectResponse("/setup", status_code=303)
    if request.state.current_user is None:
        return unauthenticated_response(request)
    return await call_next(request)


app.include_router(auth.router)
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
app.include_router(users.router)
app.include_router(users.page_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
