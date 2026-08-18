"""MCP stdio server exposing financial-email-tracker data/actions to AI agents.

Run with: python -m app.mcp.server

Every tool resolves `owner_user_id` from `MCP_OWNER_USER_ID` and scopes every
query through it - this is a single-user-per-process server, never a
cross-user one. Write tools additionally require `MCP_ALLOW_WRITE=true`, and
`send_line_daily_summary` requires the separate `MCP_ALLOW_SEND=true` since it
has a real, user-visible external side effect.

See docs/mcp-agent-config.md for agent configuration and security notes.
"""

import asyncio
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP

from app.classification import history
from app.config import Settings, get_settings
from app.gmail.authorize import token_exists, user_token_path
from app.gmail.client import GmailClient
from app.gmail.reader import GmailReader
from app.ingestion.service import IngestionAlreadyRunningError
from app.ingestion.service import run_ingestion as _run_ingestion
from app.integrations import line
from app.storage import queries
from app.storage.database import get_connection

logger = logging.getLogger(__name__)

MAX_SEARCH_PAGE_SIZE = 50
MAX_MAPPING_FIELD_LENGTH = 200

INGESTION_WINDOWS = {"default": None, "last_7_days": 7, "last_30_days": 30}

_SENSITIVE_RAW_FIELD_TOKENS = ("account", "reference", "ref_no", "email", "phone", "card")
_SENSITIVE_ARG_TOKENS = ("token", "password", "secret", "body", "raw")

mcp = FastMCP("financial-email-tracker")


class _StaticTokenVerifier:
    """Verifies a single static bearer token (constant-time compare)."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if secrets.compare_digest(token, self._token):
            return AccessToken(token=token, client_id="mcp-client", scopes=["read"])
        return None


class MCPConfigError(RuntimeError):
    """Raised when required MCP configuration (owner user id) is missing."""


def get_mcp_owner_user_id(settings: Settings | None = None) -> int:
    """Resolve the single owner_user_id this MCP process acts as. Fails fast if unset."""
    settings = settings or get_settings()
    if not settings.MCP_OWNER_USER_ID:
        raise MCPConfigError("MCP_OWNER_USER_ID is required to run the MCP server")
    return int(settings.MCP_OWNER_USER_ID)


def _require_write_enabled(settings: Settings) -> None:
    if not settings.MCP_ALLOW_WRITE:
        raise PermissionError("MCP write tools are disabled (set MCP_ALLOW_WRITE=true to enable)")


def _require_send_enabled(settings: Settings) -> None:
    if not settings.MCP_ALLOW_SEND:
        raise PermissionError("MCP send tools are disabled (set MCP_ALLOW_SEND=true to enable)")


def _mask_value(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _mask_args(kwargs: dict) -> dict:
    """Mask likely-sensitive argument values before they hit the audit log."""
    masked = {}
    for key, value in kwargs.items():
        if any(token in key.lower() for token in _SENSITIVE_ARG_TOKENS):
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


def _sanitize_raw_fields(raw_fields: dict) -> dict:
    sanitized = {}
    for key, value in raw_fields.items():
        if any(token in key.lower() for token in _SENSITIVE_RAW_FIELD_TOKENS) and isinstance(value, str):
            sanitized[key] = _mask_value(value)
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_transaction(row: dict, expose_raw: bool) -> dict:
    """Strip (or mask) raw_fields per MCP_EXPOSE_RAW_EMAIL - never leak it unmasked."""
    tx = dict(row)
    if expose_raw:
        tx["raw_fields"] = _sanitize_raw_fields(tx.get("raw_fields") or {})
    else:
        tx.pop("raw_fields", None)
    return tx


_UNKNOWN_EMAIL_FIELDS = ("id", "subject", "sender", "amount", "warnings", "received_at", "status", "transaction_code")


def _sanitize_unknown_email(row: dict) -> dict:
    """Never include raw_fields_json/body - only the fields the security plan allows."""
    return {key: row.get(key) for key in _UNKNOWN_EMAIL_FIELDS}


def _query_for_window(base_query: str, window: str) -> str:
    days = INGESTION_WINDOWS[window]
    if days is None:
        return base_query
    query = re.sub(r"\s*newer_than:\S+", "", base_query).strip()
    return f"{query} newer_than:{days}d"


async def _call_tool(name: str, owner_user_id: int | None, fn, **kwargs):
    """Run a tool body with audit logging: tool name, owner, masked args, success/duration.

    Never logs raw bodies/tokens/secrets - see _mask_args.
    """
    started = time.monotonic()
    try:
        result = await fn()
        logger.info(
            f"mcp_tool_call tool={name} owner_user_id={owner_user_id} "
            f"args={_mask_args(kwargs)} success=True duration_ms={(time.monotonic() - started) * 1000:.2f}"
        )
        return result
    except Exception as e:
        logger.info(
            f"mcp_tool_call tool={name} owner_user_id={owner_user_id} "
            f"args={_mask_args(kwargs)} success=False error={type(e).__name__} "
            f"duration_ms={(time.monotonic() - started) * 1000:.2f}"
        )
        raise


# ---- Phase 1: Read-only tools ----------------------------------------------


@mcp.tool()
async def get_dashboard_summary() -> dict[str, Any]:
    """Today's income/expense totals, transaction/uncategorized/parse-error counts, and last sync time."""
    settings = get_settings()
    owner_user_id = get_mcp_owner_user_id(settings)

    async def _run():
        db = await get_connection()
        try:
            return await queries.get_dashboard_stats(db, owner_user_id=owner_user_id)
        finally:
            await db.close()

    return await _call_tool("get_dashboard_summary", owner_user_id, _run)


@mcp.tool()
async def search_transactions(
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    transaction_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Search/filter the owner's transactions, sorted occurred_at DESC. page_size is capped at 50."""
    settings = get_settings()
    owner_user_id = get_mcp_owner_user_id(settings)
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_SEARCH_PAGE_SIZE)

    async def _run():
        db = await get_connection()
        try:
            rows, total = await queries.list_transactions(
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
            )
            items = [_sanitize_transaction(row, settings.MCP_EXPOSE_RAW_EMAIL) for row in rows]
            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            await db.close()

    return await _call_tool(
        "search_transactions",
        owner_user_id,
        _run,
        date_from=date_from,
        date_to=date_to,
        category=category,
        transaction_type=transaction_type,
        direction=direction,
        search=search,
        page=page,
        page_size=page_size,
    )


@mcp.tool()
async def get_transaction_detail(transaction_id: int) -> dict[str, Any]:
    """One transaction by id, scoped to the owner. Returns {"error": "not_found"} if missing or owned by someone else."""
    settings = get_settings()
    owner_user_id = get_mcp_owner_user_id(settings)

    async def _run():
        db = await get_connection()
        try:
            row = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
            if row is None:
                return {"error": "not_found", "transaction_id": transaction_id}
            return _sanitize_transaction(row, settings.MCP_EXPOSE_RAW_EMAIL)
        finally:
            await db.close()

    return await _call_tool("get_transaction_detail", owner_user_id, _run, transaction_id=transaction_id)


@mcp.tool()
async def list_unknown_emails(status: str | None = None, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """List emails the parser could not understand. Never includes raw email body or raw_fields."""
    settings = get_settings()
    owner_user_id = get_mcp_owner_user_id(settings)
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_SEARCH_PAGE_SIZE)

    async def _run():
        db = await get_connection()
        try:
            rows, total = await queries.list_unknown(
                db, page=page, page_size=page_size, owner_user_id=owner_user_id, status=status
            )
            items = [_sanitize_unknown_email(row) for row in rows]
            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            await db.close()

    return await _call_tool("list_unknown_emails", owner_user_id, _run, status=status, page=page, page_size=page_size)


@mcp.tool()
async def list_category_mappings() -> dict[str, Any]:
    """List the owner's current counterparty -> category mapping rules."""
    settings = get_settings()
    owner_user_id = get_mcp_owner_user_id(settings)

    async def _run():
        db = await get_connection()
        try:
            rows = await queries.list_mappings(db, owner_user_id=owner_user_id)
            return {"items": rows}
        finally:
            await db.close()

    return await _call_tool("list_category_mappings", owner_user_id, _run)


@mcp.tool()
async def get_daily_summary(day: str | None = None) -> dict[str, Any]:
    """Preview the daily LINE summary (structured data + formatted text) without sending it."""
    settings = get_settings()
    owner_user_id = get_mcp_owner_user_id(settings)

    async def _run():
        db = await get_connection()
        try:
            data = await queries.get_daily_summary_data(db, day=day, owner_user_id=owner_user_id)
        finally:
            await db.close()
        return {"data": data, "line_text": line.format_daily_summary(data)}

    return await _call_tool("get_daily_summary", owner_user_id, _run, day=day)


# ---- Phase 2: Safe write tools ----------------------------------------------


@asynccontextmanager
async def _verified_active_owner(owner_user_id: int):
    """Open a connection, yield True iff the user exists and is_active=1, close it.

    Phase 2 write tools use this to enforce the same active-user guard the
    web auth_middleware applies - so a disabled user can never be written
    to via MCP, even if their `MCP_OWNER_USER_ID` is still configured.
    """
    db = await get_connection()
    try:
        user = await queries.get_user(db, owner_user_id)
        yield bool(user and user.get("is_active"))
    finally:
        await db.close()


@mcp.tool()
async def update_transaction_category(transaction_id: int, category: str) -> dict[str, Any]:
    """Set a transaction's category and remember it for that counterparty. Requires MCP_ALLOW_WRITE=true."""
    settings = get_settings()
    _require_write_enabled(settings)
    owner_user_id = get_mcp_owner_user_id(settings)
    category = (category or "").strip()
    if not category:
        raise ValueError("category is required")

    async def _run():
        db = await get_connection()
        try:
            existing = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
            if existing is None:
                return {"error": "not_found", "transaction_id": transaction_id}
            await queries.update_transaction_category(
                db, transaction_id, category, category_source="manual", owner_user_id=owner_user_id
            )
            await history.record(
                db, existing.get("counterparty"), category, source="manual", owner_user_id=owner_user_id
            )
            await db.commit()
            updated = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
            return _sanitize_transaction(updated, settings.MCP_EXPOSE_RAW_EMAIL)
        finally:
            await db.close()

    return await _call_tool(
        "update_transaction_category", owner_user_id, _run, transaction_id=transaction_id, category=category
    )


@mcp.tool()
async def ignore_transaction(transaction_id: int, ignored: bool = True) -> dict[str, Any]:
    """Mark a transaction ignored/unignored. Requires MCP_ALLOW_WRITE=true."""
    settings = get_settings()
    _require_write_enabled(settings)
    owner_user_id = get_mcp_owner_user_id(settings)

    async def _run():
        db = await get_connection()
        try:
            existing = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
            if existing is None:
                return {"error": "not_found", "transaction_id": transaction_id}
            await queries.set_transaction_ignored(db, transaction_id, ignored, owner_user_id=owner_user_id)
            updated = await queries.get_transaction(db, transaction_id, owner_user_id=owner_user_id)
            return _sanitize_transaction(updated, settings.MCP_EXPOSE_RAW_EMAIL)
        finally:
            await db.close()

    return await _call_tool(
        "ignore_transaction", owner_user_id, _run, transaction_id=transaction_id, ignored=ignored
    )


@mcp.tool()
async def create_category_mapping(counterparty: str, category: str) -> dict[str, Any]:
    """Create/update a counterparty -> category mapping rule. Requires MCP_ALLOW_WRITE=true."""
    settings = get_settings()
    _require_write_enabled(settings)
    owner_user_id = get_mcp_owner_user_id(settings)
    counterparty = (counterparty or "").strip()
    category = (category or "").strip()
    if not counterparty or not category:
        raise ValueError("counterparty and category are required")
    if len(counterparty) > MAX_MAPPING_FIELD_LENGTH or len(category) > MAX_MAPPING_FIELD_LENGTH:
        raise ValueError(f"counterparty and category must each be <= {MAX_MAPPING_FIELD_LENGTH} characters")

    async def _run():
        db = await get_connection()
        try:
            return await queries.create_mapping(
                db, counterparty, category, source="manual", owner_user_id=owner_user_id
            )
        finally:
            await db.close()

    return await _call_tool(
        "create_category_mapping", owner_user_id, _run, counterparty=counterparty, category=category
    )


@mcp.tool()
async def run_ingestion(window: str = "default") -> dict[str, Any]:
    """Fetch new Gmail messages and ingest them. window: default|last_7_days|last_30_days.

    Requires MCP_ALLOW_WRITE=true and a connected Gmail token for the owner.
    Does not accept an arbitrary Gmail query from the agent.
    """
    settings = get_settings()
    _require_write_enabled(settings)
    owner_user_id = get_mcp_owner_user_id(settings)
    if window not in INGESTION_WINDOWS:
        raise ValueError(f"window must be one of {sorted(INGESTION_WINDOWS)}")

    # Verify the owner is still active before doing any work - matches what
    # the web auth_middleware enforces. Without this an admin could disable
    # a user, but the local MCP process would still ingest into their scope.
    async with _verified_active_owner(owner_user_id) as active:
        if not active:
            raise PermissionError(f"User {owner_user_id} is not active; cannot run ingestion")

        token_path = user_token_path(owner_user_id)
        if not token_exists(token_path):
            raise RuntimeError(f"Connect Gmail for user {owner_user_id} before running ingestion")

        async def _run():
            query = _query_for_window(settings.GMAIL_QUERY, window)
            # Per-user token file already embeds client_id/client_secret/refresh_token,
            # so no separate credentials_path is needed (see app/gmail/authorize.py).
            reader = GmailReader(GmailClient(token_path=token_path))
            try:
                return await _run_ingestion(query, reader=reader, owner_user_id=owner_user_id)
            except IngestionAlreadyRunningError as e:
                return {"error": "already_running", "detail": str(e)}

        return await _call_tool("run_ingestion", owner_user_id, _run, window=window)

    async def _run():
        query = _query_for_window(settings.GMAIL_QUERY, window)
        # Per-user token file already embeds client_id/client_secret/refresh_token,
        # so no separate credentials_path is needed (see app/gmail/authorize.py).
        reader = GmailReader(GmailClient(token_path=token_path))
        try:
            return await _run_ingestion(query, reader=reader, owner_user_id=owner_user_id)
        except IngestionAlreadyRunningError as e:
            return {"error": "already_running", "detail": str(e)}

    return await _call_tool("run_ingestion", owner_user_id, _run, window=window)


@mcp.tool()
async def send_line_daily_summary(day: str | None = None) -> dict[str, Any]:
    """Send the daily summary to LINE for real. Requires MCP_ALLOW_SEND=true (separate from MCP_ALLOW_WRITE)."""
    settings = get_settings()
    _require_send_enabled(settings)
    owner_user_id = get_mcp_owner_user_id(settings)

    async def _run():
        db = await get_connection()
        try:
            data = await queries.get_daily_summary_data(db, day=day, owner_user_id=owner_user_id)
        finally:
            await db.close()
        text = line.format_daily_summary(data)
        sent = await line.send_message(settings.LINE_USER_ID, text, settings.LINE_CHANNEL_ACCESS_TOKEN)
        return {"sent": sent, "line_text": text, "date": data["date"]}

    return await _call_tool("send_line_daily_summary", owner_user_id, _run, day=day)


def _configure_http(settings) -> None:
    """Apply host/port/token settings to the mcp instance for HTTP transports."""
    if not settings.MCP_API_TOKEN:
        raise RuntimeError(
            "MCP_API_TOKEN must be set when using HTTP transport (sse or streamable-http)."
        )
    mcp.settings.host = settings.MCP_HOST
    mcp.settings.port = settings.MCP_PORT
    mcp._token_verifier = _StaticTokenVerifier(settings.MCP_API_TOKEN)


def main() -> None:
    settings = get_settings()
    if not settings.MCP_ENABLED:
        raise SystemExit("MCP server is disabled. Set MCP_ENABLED=true to run it.")
    get_mcp_owner_user_id(settings)  # fail fast if not configured

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


if __name__ == "__main__":
    main()
