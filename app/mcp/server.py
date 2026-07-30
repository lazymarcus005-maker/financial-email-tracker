"""MCP server for Financial Email Tracker - Phase 1 read-only.

Transports:
    stdio (default)  — for local agents (Claude Desktop, Cursor, etc.)
    sse              — HTTP Server-Sent Events, requires MCP_API_TOKEN
    streamable-http  — HTTP streaming (MCP spec 2025-03-26), requires MCP_API_TOKEN

Run via:
    python -m app.mcp.server

Required env:
    MCP_OWNER_USER_ID   user id whose data this server exposes
    DATABASE_PATH       path to SQLite file (or DATABASE_BACKEND=postgres + DATABASE_URL)

Optional env:
    MCP_TRANSPORT       stdio | sse | streamable-http  (default: stdio)
    MCP_HOST            bind host for HTTP transports  (default: 0.0.0.0)
    MCP_PORT            bind port for HTTP transports  (default: 8001)
    MCP_API_TOKEN       bearer token required for HTTP transports
"""

import asyncio
import logging
import secrets

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.integrations.line import format_daily_summary
from app.storage import queries
from app.storage.database import get_connection

logger = logging.getLogger(__name__)

mcp = FastMCP("financial-email-tracker")


class _StaticTokenVerifier:
    """Verifies a single static bearer token (constant-time compare)."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if secrets.compare_digest(token, self._token):
            return AccessToken(token=token, client_id="mcp-client", scopes=["read"])
        return None


def _owner_user_id() -> int:
    uid = get_settings().MCP_OWNER_USER_ID
    if uid is None:
        raise RuntimeError("MCP_OWNER_USER_ID is not set.")
    return int(uid)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_dashboard_summary() -> dict:
    """Return today's financial overview: income, expense, totals, uncategorized count, last sync."""
    db = await get_connection()
    try:
        return dict(await queries.get_dashboard_stats(db, owner_user_id=_owner_user_id()))
    finally:
        await db.close()


@mcp.tool()
async def search_transactions(
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Search and filter transactions.

    Args:
        date_from: start date YYYY-MM-DD (inclusive)
        date_to:   end date YYYY-MM-DD (inclusive)
        category:  exact category name
        direction: 'in' for income, 'out' for expense
        search:    keyword matched against counterparty and description
        page:      1-based page number
        page_size: results per page, max 50
    """
    page_size = min(max(1, page_size), 50)
    db = await get_connection()
    try:
        items, total = await queries.list_transactions(
            db,
            owner_user_id=_owner_user_id(),
            date_from=date_from,
            date_to=date_to,
            category=category,
            direction=direction,
            search=search,
            page=page,
            page_size=page_size,
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        await db.close()


@mcp.tool()
async def get_transaction_detail(transaction_id: int) -> dict:
    """Return full detail of a single transaction by its database id."""
    db = await get_connection()
    try:
        row = await queries.get_transaction(db, transaction_id, owner_user_id=_owner_user_id())
        if row is None:
            return {"error": "Transaction not found"}
        return row
    finally:
        await db.close()


@mcp.tool()
async def list_unknown_emails(
    status: str = "pending",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """List emails that could not be parsed.

    Args:
        status:    'pending' (default) or 'ignored'
        page:      1-based page number
        page_size: results per page, max 50
    """
    page_size = min(max(1, page_size), 50)
    db = await get_connection()
    try:
        items, total = await queries.list_unknown(
            db,
            owner_user_id=_owner_user_id(),
            status=status,
            page=page,
            page_size=page_size,
        )
        # raw_fields_json not exposed in Phase 1
        safe = [{k: v for k, v in item.items() if k != "raw_fields_json"} for item in items]
        return {"items": safe, "total": total, "page": page, "page_size": page_size}
    finally:
        await db.close()


@mcp.tool()
async def get_daily_summary(day: str | None = None) -> dict:
    """Return a day's financial summary in structured data and LINE message format.

    Args:
        day: date YYYY-MM-DD, defaults to today
    """
    db = await get_connection()
    try:
        data = await queries.get_daily_summary_data(db, day=day, owner_user_id=_owner_user_id())
        return {"data": data, "line_text": format_daily_summary(data)}
    finally:
        await db.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def _configure_http(settings) -> None:
    """Apply host/port/token settings to the mcp instance for HTTP transports."""
    if not settings.MCP_API_TOKEN:
        raise RuntimeError(
            "MCP_API_TOKEN must be set when using HTTP transport (sse or streamable-http)."
        )
    mcp.settings.host = settings.MCP_HOST
    mcp.settings.port = settings.MCP_PORT
    mcp._token_verifier = _StaticTokenVerifier(settings.MCP_API_TOKEN)


if __name__ == "__main__":
    from app.logging_config import configure_logging

    settings = get_settings()
    configure_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)

    _owner_user_id()  # fail fast if not configured

    transport = settings.MCP_TRANSPORT

    if transport == "stdio":
        logger.info("Starting MCP server (stdio)")
        mcp.run(transport="stdio")

    elif transport == "sse":
        _configure_http(settings)
        logger.info("Starting MCP server (SSE) on %s:%s", settings.MCP_HOST, settings.MCP_PORT)
        asyncio.run(mcp.run_sse_async())

    elif transport == "streamable-http":
        _configure_http(settings)
        logger.info(
            "Starting MCP server (streamable-http) on %s:%s", settings.MCP_HOST, settings.MCP_PORT
        )
        asyncio.run(mcp.run_streamable_http_async())

    else:
        raise SystemExit(f"Unknown MCP_TRANSPORT: {transport!r}. Use stdio, sse, or streamable-http.")
