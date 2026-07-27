"""Main web app - Routes and middleware."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Financial Email Tracker",
    description="Track financial transactions from Gmail",
    version="2.0.0",
)

# Serve static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/")
async def index():
    """Dashboard home page."""
    # TODO: Render dashboard template
    return {"message": "Financial Email Tracker - Dashboard"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
