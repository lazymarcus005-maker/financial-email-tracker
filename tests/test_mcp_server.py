"""Tests for app.mcp.server - owner scoping, write/send guards, and audit-safe output.

Uses the `db_connection`/`temp_db_path` fixtures from conftest.py, which point
`app.storage.database.DATABASE_PATH` (and hence app.mcp.server's `get_connection`
calls) at a fresh temp SQLite file per test.
"""

import pytest

from app.classification import history
from app.config import Settings
from app.gmail import authorize
from app.mcp import server as mcp_server


def _settings(**overrides) -> Settings:
    base = dict(
        MCP_OWNER_USER_ID="1",
        MCP_ALLOW_WRITE=False,
        MCP_ALLOW_SEND=False,
        MCP_EXPOSE_RAW_EMAIL=False,
        GMAIL_QUERY="from:bank newer_than:90d",
        LINE_USER_ID="line-user",
        LINE_CHANNEL_ACCESS_TOKEN="line-secret-token",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def owner_settings(monkeypatch):
    """Default settings: owner=1, all writes/sends disabled. Tests override via monkeypatch again."""
    settings = _settings()
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    return settings


def _patch_settings(monkeypatch, **overrides) -> Settings:
    settings = _settings(**overrides)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    return settings


async def _insert_transaction(db, owner_user_id, suffix="1", **overrides):
    fields = dict(
        owner_user_id=owner_user_id,
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2026-07-28T10:00:00",
        amount=100.0,
        category="Food",
        category_source="manual",
        bank="KBank",
        parse_status="complete",
        counterparty="Shopee",
        raw_fields_json='{"account_number": "1234567890", "note": "lunch"}',
        gmail_message_id=f"msg-{owner_user_id}-{suffix}",
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cursor = await db.execute(
        f"INSERT INTO transactions ({columns}) VALUES ({placeholders})", list(fields.values())
    )
    await db.commit()
    return cursor.lastrowid


async def _insert_unknown(db, owner_user_id, suffix="1", **overrides):
    fields = dict(
        owner_user_id=owner_user_id,
        subject="Unrecognized bank email",
        sender="notify@some-bank.com",
        amount=50.0,
        warnings_json='["low_confidence"]',
        raw_fields_json='{"body": "SENSITIVE RAW EMAIL BODY TEXT"}',
        status="pending",
        gmail_message_id=f"unk-{owner_user_id}-{suffix}",
        received_at="2026-07-28T09:00:00",
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cursor = await db.execute(
        f"INSERT INTO unknown_patterns ({columns}) VALUES ({placeholders})", list(fields.values())
    )
    await db.commit()
    return cursor.lastrowid


# ---- Owner scoping -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dashboard_summary_scopes_to_owner(db_connection, owner_settings):
    await _insert_transaction(db_connection, owner_user_id=1, suffix="a", amount=100.0)
    await _insert_transaction(db_connection, owner_user_id=2, suffix="b", amount=999.0)

    result = await mcp_server.get_dashboard_summary()

    assert result["total_transactions"] == 1


@pytest.mark.asyncio
async def test_search_transactions_scopes_to_owner(db_connection, owner_settings):
    await _insert_transaction(db_connection, owner_user_id=1, suffix="a", counterparty="Owner1Shop")
    await _insert_transaction(db_connection, owner_user_id=2, suffix="b", counterparty="Owner2Shop")

    result = await mcp_server.search_transactions()

    assert result["total"] == 1
    assert [item["counterparty"] for item in result["items"]] == ["Owner1Shop"]


@pytest.mark.asyncio
async def test_get_transaction_detail_hides_other_owners_transaction(db_connection, owner_settings):
    other_owner_tx_id = await _insert_transaction(db_connection, owner_user_id=2, suffix="b")

    result = await mcp_server.get_transaction_detail(transaction_id=other_owner_tx_id)

    assert result == {"error": "not_found", "transaction_id": other_owner_tx_id}


@pytest.mark.asyncio
async def test_get_transaction_detail_returns_owners_transaction(db_connection, owner_settings):
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a")

    result = await mcp_server.get_transaction_detail(transaction_id=tx_id)

    assert result["id"] == tx_id
    assert result["counterparty"] == "Shopee"


@pytest.mark.asyncio
async def test_list_unknown_emails_scopes_to_owner_and_omits_raw(db_connection, owner_settings):
    await _insert_unknown(db_connection, owner_user_id=1, suffix="a")
    await _insert_unknown(db_connection, owner_user_id=2, suffix="b")

    result = await mcp_server.list_unknown_emails()

    assert result["total"] == 1
    item = result["items"][0]
    assert set(item.keys()) == set(mcp_server._UNKNOWN_EMAIL_FIELDS)
    assert "raw_fields" not in item
    assert "SENSITIVE RAW EMAIL BODY TEXT" not in str(item)


@pytest.mark.asyncio
async def test_list_category_mappings_scopes_to_owner(db_connection, owner_settings):
    from app.storage import queries

    await queries.create_mapping(db_connection, "Owner1Merchant", "Food", owner_user_id=1)
    await queries.create_mapping(db_connection, "Owner2Merchant", "Food", owner_user_id=2)

    result = await mcp_server.list_category_mappings()

    counterparties = [m["counterparty"] for m in result["items"]]
    assert counterparties == ["Owner1Merchant"]


# ---- search_transactions page_size cap ---------------------------------------


@pytest.mark.asyncio
async def test_search_transactions_caps_page_size(db_connection, owner_settings):
    for i in range(60):
        await _insert_transaction(db_connection, owner_user_id=1, suffix=str(i))

    result = await mcp_server.search_transactions(page_size=1000)

    assert result["page_size"] == mcp_server.MAX_SEARCH_PAGE_SIZE
    assert len(result["items"]) == mcp_server.MAX_SEARCH_PAGE_SIZE


# ---- Raw field exposure -------------------------------------------------------


@pytest.mark.asyncio
async def test_transaction_omits_raw_fields_by_default(db_connection, owner_settings):
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a")

    result = await mcp_server.get_transaction_detail(transaction_id=tx_id)

    assert "raw_fields" not in result


@pytest.mark.asyncio
async def test_transaction_masks_sensitive_raw_fields_when_exposed(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_EXPOSE_RAW_EMAIL=True)
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a")

    result = await mcp_server.get_transaction_detail(transaction_id=tx_id)

    assert result["raw_fields"]["account_number"] != "1234567890"
    assert result["raw_fields"]["account_number"].endswith("7890")
    assert result["raw_fields"]["note"] == "lunch"


# ---- get_daily_summary matches LINE formatting logic --------------------------


@pytest.mark.asyncio
async def test_get_daily_summary_matches_line_format(db_connection, owner_settings):
    from app.integrations import line

    await _insert_transaction(
        db_connection,
        owner_user_id=1,
        suffix="a",
        occurred_at="2026-07-28T08:00:00",
        direction="in",
        amount=500.0,
        category=None,
    )
    await _insert_transaction(
        db_connection,
        owner_user_id=1,
        suffix="b",
        occurred_at="2026-07-28T09:00:00",
        direction="out",
        amount=120.0,
        category="Food",
    )

    result = await mcp_server.get_daily_summary(day="2026-07-28")

    assert result["line_text"] == line.format_daily_summary(result["data"])
    assert result["data"]["income_total"] == 500.0


# ---- Write tools: guarded by MCP_ALLOW_WRITE -----------------------------------


@pytest.mark.asyncio
async def test_update_transaction_category_fails_when_write_disabled(db_connection, owner_settings):
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a")

    with pytest.raises(PermissionError):
        await mcp_server.update_transaction_category(transaction_id=tx_id, category="Transport")


@pytest.mark.asyncio
async def test_ignore_transaction_fails_when_write_disabled(db_connection, owner_settings):
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a")

    with pytest.raises(PermissionError):
        await mcp_server.ignore_transaction(transaction_id=tx_id, ignored=True)


@pytest.mark.asyncio
async def test_create_category_mapping_fails_when_write_disabled(db_connection, owner_settings):
    with pytest.raises(PermissionError):
        await mcp_server.create_category_mapping(counterparty="Grab", category="Transport")


@pytest.mark.asyncio
async def test_run_ingestion_fails_when_write_disabled(db_connection, owner_settings):
    with pytest.raises(PermissionError):
        await mcp_server.run_ingestion(window="default")


@pytest.mark.asyncio
async def test_send_line_daily_summary_fails_when_send_disabled(db_connection, monkeypatch):
    # MCP_ALLOW_WRITE=True but MCP_ALLOW_SEND is a *separate* flag and stays off.
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True, MCP_ALLOW_SEND=False)

    with pytest.raises(PermissionError):
        await mcp_server.send_line_daily_summary()


# ---- Write tools: succeed when MCP_ALLOW_WRITE=true ----------------------------


@pytest.mark.asyncio
async def test_update_transaction_category_succeeds_and_records_history(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a", counterparty="Shopee")

    result = await mcp_server.update_transaction_category(transaction_id=tx_id, category="Shopping")

    assert result["category"] == "Shopping"
    assert result["category_source"] == "manual"
    recorded = await history.lookup(db_connection, "Shopee", owner_user_id=1)
    assert recorded == "Shopping"


@pytest.mark.asyncio
async def test_update_transaction_category_rejects_empty_category(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a")

    with pytest.raises(ValueError):
        await mcp_server.update_transaction_category(transaction_id=tx_id, category="   ")


@pytest.mark.asyncio
async def test_update_transaction_category_not_found_for_other_owner(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)
    other_owner_tx_id = await _insert_transaction(db_connection, owner_user_id=2, suffix="b")

    result = await mcp_server.update_transaction_category(transaction_id=other_owner_tx_id, category="Shopping")

    assert result == {"error": "not_found", "transaction_id": other_owner_tx_id}


@pytest.mark.asyncio
async def test_ignore_transaction_succeeds(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)
    tx_id = await _insert_transaction(db_connection, owner_user_id=1, suffix="a", parse_status="complete")

    result = await mcp_server.ignore_transaction(transaction_id=tx_id, ignored=True)

    assert result["parse_status"] == "ignored"


@pytest.mark.asyncio
async def test_create_category_mapping_succeeds(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)

    result = await mcp_server.create_category_mapping(counterparty="Grab", category="Transport")

    assert result["counterparty"] == "Grab"
    assert result["category"] == "Transport"
    assert result["owner_user_id"] == 1


@pytest.mark.asyncio
async def test_create_category_mapping_rejects_empty_fields(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)

    with pytest.raises(ValueError):
        await mcp_server.create_category_mapping(counterparty="", category="Transport")


@pytest.mark.asyncio
async def test_create_category_mapping_rejects_overlong_fields(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)

    with pytest.raises(ValueError):
        await mcp_server.create_category_mapping(counterparty="x" * 300, category="Transport")


# ---- run_ingestion: window enum only, never an arbitrary Gmail query -----------


@pytest.mark.asyncio
async def test_run_ingestion_rejects_arbitrary_window(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True)

    with pytest.raises(ValueError):
        await mcp_server.run_ingestion(window="from:anything OR is:unread")


@pytest.mark.asyncio
async def test_run_ingestion_requires_connected_gmail_token(db_connection, monkeypatch, tmp_path):
    monkeypatch.setattr(authorize, "USER_TOKEN_ROOT", tmp_path / "gmail-users")
    # Owner must exist + be active for ingestion to reach the Gmail-token check.
    cursor = await db_connection.execute(
        "INSERT INTO users (email, display_name, password_hash, role, is_active) "
        "VALUES (?, ?, ?, 'admin', 1)",
        ("owner@example.com", "Owner", "x"),
    )
    await db_connection.commit()
    user_id = cursor.lastrowid
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True, MCP_OWNER_USER_ID=str(user_id))

    with pytest.raises(RuntimeError, match="Connect Gmail"):
        await mcp_server.run_ingestion(window="default")


@pytest.mark.asyncio
async def test_run_ingestion_rejects_inactive_user(db_connection, monkeypatch, tmp_path):
    """A disabled user cannot trigger ingestion, even if their MCP_OWNER_USER_ID is set."""
    monkeypatch.setattr(authorize, "USER_TOKEN_ROOT", tmp_path / "gmail-users")
    cursor = await db_connection.execute(
        "INSERT INTO users (email, display_name, password_hash, role, is_active) "
        "VALUES (?, ?, ?, 'admin', 0)",
        ("disabled@example.com", "Disabled", "x"),
    )
    await db_connection.commit()
    user_id = cursor.lastrowid
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True, MCP_OWNER_USER_ID=str(user_id))

    with pytest.raises(PermissionError, match="not active"):
        await mcp_server.run_ingestion(window="default")


def test_query_for_window_only_accepts_known_windows():
    assert set(mcp_server.INGESTION_WINDOWS) == {"default", "last_7_days", "last_30_days"}
    assert mcp_server._query_for_window("from:bank newer_than:90d", "default") == "from:bank newer_than:90d"
    assert mcp_server._query_for_window("from:bank newer_than:90d", "last_7_days") == "from:bank newer_than:7d"


# ---- No secrets/raw tokens leak in output or audit log --------------------------


def test_mask_args_hides_sensitive_keys():
    masked = mcp_server._mask_args(
        {"gmail_token": "abc123", "password": "hunter2", "counterparty": "Grab", "raw_body": "secret text"}
    )
    assert masked["gmail_token"] == "***"
    assert masked["password"] == "***"
    assert masked["raw_body"] == "***"
    assert masked["counterparty"] == "Grab"


@pytest.mark.asyncio
async def test_send_line_daily_summary_response_excludes_channel_token(db_connection, monkeypatch):
    _patch_settings(monkeypatch, MCP_ALLOW_WRITE=True, MCP_ALLOW_SEND=True)

    async def _fake_send_message(user_id, text, channel_access_token, timeout=10.0):
        return True

    monkeypatch.setattr(mcp_server.line, "send_message", _fake_send_message)

    result = await mcp_server.send_line_daily_summary(day="2026-07-28")

    assert result["sent"] is True
    assert "line-secret-token" not in str(result)
    assert set(result.keys()) == {"sent", "line_text", "date"}


# ---- Owner resolution ------------------------------------------------------------


def test_get_mcp_owner_user_id_requires_config():
    settings = _settings(MCP_OWNER_USER_ID=None)

    with pytest.raises(mcp_server.MCPConfigError):
        mcp_server.get_mcp_owner_user_id(settings)


def test_get_mcp_owner_user_id_returns_int():
    settings = _settings(MCP_OWNER_USER_ID="7")

    assert mcp_server.get_mcp_owner_user_id(settings) == 7
