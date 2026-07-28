"""Tests for the FastAPI web routes - status codes, DB round trips, no leaked secrets."""

import json

import pytest
from fastapi.testclient import TestClient

from app.web import deps
from app.web.main import app
from app.web.routes import ingestion as ingestion_routes
from app.web.routes import transactions as transactions_routes
from app.web.routes import unknown as unknown_routes


@pytest.fixture
def client(temp_db_path):
    app.dependency_overrides[deps.get_gmail_client] = lambda: object()
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


async def _insert_transaction(db, **overrides):
    fields = dict(
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2026-07-27T10:00:00",
        amount=100.0,
        fee=0.0,
        available_balance=None,
        counterparty="Shopee",
        description="Payment",
        category="Shopping",
        category_source="rule",
        parser_version="1.0",
        parse_status="complete",
        parse_confidence=1.0,
        warnings_json="[]",
        raw_fields_json="{}",
        gmail_message_id="msg-1",
    )
    fields.update(overrides)
    cursor = await db.execute(
        """
        INSERT INTO transactions (
            transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (:transaction_type, :direction, :status, :occurred_at, :amount, :fee,
            :available_balance, :counterparty, :description, :category, :category_source,
            :parser_version, :parse_status, :parse_confidence, :warnings_json,
            :raw_fields_json, :gmail_message_id)
        """,
        fields,
    )
    await db.commit()
    return cursor.lastrowid


async def _insert_unknown(db, **overrides):
    fields = dict(
        subject="Unrecognized email",
        sender="unknown@example.com",
        transaction_code=None,
        amount=None,
        warnings_json="[]",
        raw_fields_json="{}",
        parser_version="1.0",
        status="pending",
        gmail_message_id="msg-unknown-1",
    )
    fields.update(overrides)
    cursor = await db.execute(
        """
        INSERT INTO unknown_patterns (
            subject, sender, transaction_code, amount, warnings_json,
            raw_fields_json, parser_version, status, gmail_message_id
        ) VALUES (:subject, :sender, :transaction_code, :amount, :warnings_json,
            :raw_fields_json, :parser_version, :status, :gmail_message_id)
        """,
        fields,
    )
    await db.commit()
    return cursor.lastrowid


# ---- Dashboard ---------------------------------------------------------------


def test_dashboard_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "2.0.0"}


# ---- Settings ------------------------------------------------------------------


def test_settings_api_exposes_no_secrets(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "LINE_CHANNEL_ACCESS_TOKEN" not in json.dumps(body)
    assert "GMAIL_CREDENTIALS_PATH" not in body
    assert "GMAIL_TOKEN_PATH" not in body
    assert set(body) == {
        "gmail_query", "database_path", "schedule", "timezone", "ai_enabled",
        "ollama_base_url", "ollama_model", "parser_version", "line_configured", "log_level",
    }


def test_settings_page_loads(client):
    resp = client.get("/settings")
    assert resp.status_code == 200


# ---- Mappings CRUD ---------------------------------------------------------------


def test_mappings_crud_roundtrip(client):
    create_resp = client.post("/api/mappings", json={"counterparty": "Netflix", "category": "Subscription"})
    assert create_resp.status_code == 201
    mapping = create_resp.json()
    assert mapping["counterparty"] == "Netflix"
    assert mapping["category"] == "Subscription"

    list_resp = client.get("/api/mappings")
    assert list_resp.status_code == 200
    assert any(m["id"] == mapping["id"] for m in list_resp.json()["items"])

    update_resp = client.patch(f"/api/mappings/{mapping['id']}", json={"category": "Entertainment"})
    assert update_resp.status_code == 200
    assert update_resp.json()["category"] == "Entertainment"

    delete_resp = client.delete(f"/api/mappings/{mapping['id']}")
    assert delete_resp.status_code == 204

    missing_resp = client.patch(f"/api/mappings/{mapping['id']}", json={"category": "X"})
    assert missing_resp.status_code == 404


def test_mappings_page_loads(client):
    resp = client.get("/mappings")
    assert resp.status_code == 200


# ---- Transactions ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_and_get_transaction(client, db_connection):
    tx_id = await _insert_transaction(db_connection)

    list_resp = client.get("/api/transactions")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == tx_id

    detail_resp = client.get(f"/api/transactions/{tx_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["counterparty"] == "Shopee"

    assert client.get("/api/transactions/999999").status_code == 404


@pytest.mark.asyncio
async def test_transaction_filters(client, db_connection):
    await _insert_transaction(db_connection, gmail_message_id="msg-a", direction="out", category="Shopping")
    await _insert_transaction(db_connection, gmail_message_id="msg-b", direction="in", category="Income")

    resp = client.get("/api/transactions", params={"direction": "in"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["direction"] == "in"


@pytest.mark.asyncio
async def test_update_transaction_category_records_history(client, db_connection):
    tx_id = await _insert_transaction(db_connection, category=None, category_source=None)

    resp = client.patch(f"/api/transactions/{tx_id}", json={"category": "Manual Category"})
    assert resp.status_code == 200
    assert resp.json()["category"] == "Manual Category"

    cursor = await db_connection.execute(
        "SELECT category FROM counterparty_mapping WHERE counterparty = ?", ("Shopee",)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["category"] == "Manual Category"


@pytest.mark.asyncio
async def test_update_transaction_ignore_flag(client, db_connection):
    tx_id = await _insert_transaction(db_connection)

    resp = client.patch(f"/api/transactions/{tx_id}", json={"ignore": True})
    assert resp.status_code == 200
    assert resp.json()["parse_status"] == "ignored"


def test_update_missing_transaction_returns_404(client):
    resp = client.patch("/api/transactions/999999", json={"category": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_transaction(client, db_connection):
    tx_id = await _insert_transaction(db_connection)

    resp = client.delete(f"/api/transactions/{tx_id}")
    assert resp.status_code == 204

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM transactions WHERE id = ?", (tx_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["n"] == 0

    assert client.delete("/api/transactions/999999").status_code == 404


@pytest.mark.asyncio
async def test_transactions_page_loads(client, db_connection):
    await _insert_transaction(db_connection)
    resp = client.get("/transactions")
    assert resp.status_code == 200

    tx_id = (await db_connection.execute("SELECT id FROM transactions LIMIT 1"))
    row = await tx_id.fetchone()
    await tx_id.close()
    detail_resp = client.get(f"/transactions/{row['id']}")
    assert detail_resp.status_code == 200


def test_transaction_detail_page_404_for_missing(client):
    resp = client.get("/transactions/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transaction_detail_modal_route(client, db_connection):
    tx_id = await _insert_transaction(db_connection)
    resp = client.get(f"/transactions/{tx_id}/modal")
    assert resp.status_code == 200
    assert "Transaction #" in resp.text


def test_transaction_detail_modal_404_for_missing(client):
    resp = client.get("/transactions/999999/modal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transaction_raw_email_renders_fragment(client, db_connection, monkeypatch):
    tx_id = await _insert_transaction(db_connection)

    class FakeMessage:
        sender = "notify@kasikornbank.com"
        subject = "K PLUS: Transfer Successful"
        received_at = "2026-07-27 10:00:00"
        body_text = "Transaction Date: 27/07/2026\nAmount: 100.00 THB"

    class FakeGmailClient:
        def get_message(self, message_id):
            return FakeMessage()

    app.dependency_overrides[deps.get_gmail_client] = lambda: FakeGmailClient()
    resp = client.get(f"/api/transactions/{tx_id}/raw-email")
    assert resp.status_code == 200
    assert "Transaction Date: 27/07/2026" in resp.text


@pytest.mark.asyncio
async def test_transaction_raw_email_shows_error_on_gmail_failure(client, db_connection):
    tx_id = await _insert_transaction(db_connection)

    class FailingGmailClient:
        def get_message(self, message_id):
            raise RuntimeError("Gmail unreachable")

    app.dependency_overrides[deps.get_gmail_client] = lambda: FailingGmailClient()
    resp = client.get(f"/api/transactions/{tx_id}/raw-email")
    assert resp.status_code == 200
    assert "Could not load the original email" in resp.text


@pytest.mark.asyncio
async def test_reparse_transaction_not_found(client, db_connection, monkeypatch):
    async def fake_reparse_transaction(db, transaction_id, **kwargs):
        return {"status": "not_found"}

    monkeypatch.setattr(transactions_routes, "reparse_transaction", fake_reparse_transaction)

    resp = client.post("/api/reparse/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reparse_transaction_success(client, db_connection, monkeypatch):
    tx_id = await _insert_transaction(db_connection)

    async def fake_reparse_transaction(db, transaction_id, **kwargs):
        return {"status": "parsed", "transaction_id": transaction_id}

    monkeypatch.setattr(transactions_routes, "reparse_transaction", fake_reparse_transaction)

    resp = client.post(f"/api/reparse/{tx_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "parsed", "transaction_id": tx_id}


# ---- Unknown patterns -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_unknown_and_ignore(client, db_connection):
    unknown_id = await _insert_unknown(db_connection)

    list_resp = client.get("/api/unknown")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    ignore_resp = client.post(f"/api/unknown/{unknown_id}/ignore")
    assert ignore_resp.status_code == 200
    assert ignore_resp.json()["status"] == "ignored"

    assert client.post("/api/unknown/999999/ignore").status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown(client, db_connection):
    unknown_id = await _insert_unknown(db_connection)

    resp = client.delete(f"/api/unknown/{unknown_id}")
    assert resp.status_code == 204

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM unknown_patterns WHERE id = ?", (unknown_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["n"] == 0

    assert client.delete("/api/unknown/999999").status_code == 404


@pytest.mark.asyncio
async def test_unknown_page_loads(client, db_connection):
    await _insert_unknown(db_connection)
    resp = client.get("/unknown")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reparse_unknown_not_found(client, monkeypatch):
    async def fake_reparse_unknown(db, unknown_id, **kwargs):
        return {"status": "not_found"}

    monkeypatch.setattr(unknown_routes, "reparse_unknown", fake_reparse_unknown)

    resp = client.post("/api/unknown/999999/reparse")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reparse_unknown_success(client, db_connection, monkeypatch):
    unknown_id = await _insert_unknown(db_connection)

    async def fake_reparse_unknown(db, uid, **kwargs):
        return {"status": "parsed"}

    monkeypatch.setattr(unknown_routes, "reparse_unknown", fake_reparse_unknown)

    resp = client.post(f"/api/unknown/{unknown_id}/reparse")
    assert resp.status_code == 200
    assert resp.json() == {"status": "parsed"}


# ---- Ingestion --------------------------------------------------------------------


def test_trigger_ingestion_run(client, monkeypatch):
    async def fake_run_ingestion(query, engine=None):
        return {"emails_checked": 3, "inserted": 2, "duplicates": 1, "failed": 0}

    monkeypatch.setattr(ingestion_routes, "run_ingestion", fake_run_ingestion)

    resp = client.post("/api/ingestion/run")
    assert resp.status_code == 200
    assert resp.json() == {"emails_checked": 3, "inserted": 2, "duplicates": 1, "failed": 0}


def test_trigger_ingestion_run_returns_409_when_already_running(client, monkeypatch):
    async def fake_run_ingestion(query, engine=None):
        raise ingestion_routes.IngestionAlreadyRunningError("An ingestion run is already in progress")

    monkeypatch.setattr(ingestion_routes, "run_ingestion", fake_run_ingestion)

    resp = client.post("/api/ingestion/run")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_runs_empty_then_populated(client, db_connection):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    await db_connection.execute(
        "INSERT INTO ingestion_runs (emails_checked, inserted, duplicates, failed, duration_seconds) VALUES (1, 1, 0, 0, 0.5)"
    )
    await db_connection.commit()

    resp = client.get("/api/runs")
    assert resp.json()["total"] == 1


def test_retry_run_not_found(client):
    resp = client.post("/api/ingestion/retry/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_run_retries_pending_unknowns(client, db_connection, monkeypatch):
    cursor = await db_connection.execute(
        "INSERT INTO ingestion_runs (emails_checked, inserted, duplicates, failed, duration_seconds) VALUES (1, 0, 0, 1, 0.1)"
    )
    await db_connection.commit()
    run_id = cursor.lastrowid
    await _insert_unknown(db_connection)

    async def fake_reparse_unknown(db, unknown_id, **kwargs):
        return {"status": "parsed"}

    monkeypatch.setattr(ingestion_routes, "reparse_unknown", fake_reparse_unknown)

    resp = client.post(f"/api/ingestion/retry/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["retried"] == 1
    assert body["parsed"] == 1
    assert body["failed"] == 0


@pytest.mark.asyncio
async def test_unknown_detail_modal_route(client, db_connection):
    unknown_id = await _insert_unknown(db_connection)
    resp = client.get(f"/unknown/{unknown_id}/modal")
    assert resp.status_code == 200
    assert "Categorize as Transaction" in resp.text


def test_unknown_detail_modal_404_for_missing(client):
    resp = client.get("/unknown/999999/modal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_unknown_creates_transaction_and_resolves(client, db_connection):
    unknown_id = await _insert_unknown(db_connection, sender="notify@kasikornbank.com")

    resp = client.post(
        f"/api/unknown/{unknown_id}/promote",
        data={
            "transaction_type": "bank_transfer",
            "direction": "out",
            "status": "success",
            "occurred_at": "2026-07-27T10:00",
            "amount": "250.0",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 200
    assert "Promoted to" in resp.text

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id FROM unknown_patterns WHERE id = ?", (unknown_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] is not None

    cursor = await db_connection.execute(
        "SELECT category, category_source, bank FROM transactions WHERE id = ?", (row["resolved_transaction_id"],)
    )
    tx_row = await cursor.fetchone()
    await cursor.close()
    assert tx_row["category"] == "Shopping"
    assert tx_row["category_source"] == "manual"
    assert tx_row["bank"] == "KBank"


@pytest.mark.asyncio
async def test_promote_unknown_missing_required_field_returns_422(client, db_connection):
    unknown_id = await _insert_unknown(db_connection)

    resp = client.post(
        f"/api/unknown/{unknown_id}/promote",
        data={"transaction_type": "bank_transfer", "direction": "out", "status": "success"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_promote_unknown_already_resolved_returns_409(client, db_connection):
    unknown_id = await _insert_unknown(db_connection, status="resolved")

    resp = client.post(
        f"/api/unknown/{unknown_id}/promote",
        data={
            "transaction_type": "bank_transfer",
            "direction": "out",
            "status": "success",
            "occurred_at": "2026-07-27T10:00",
            "amount": "10.0",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 409


def test_promote_unknown_not_found_returns_404(client):
    resp = client.post(
        "/api/unknown/999999/promote",
        data={
            "transaction_type": "bank_transfer",
            "direction": "out",
            "status": "success",
            "occurred_at": "2026-07-27T10:00",
            "amount": "10.0",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 404
