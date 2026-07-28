"""Tests for the FastAPI web routes - status codes, DB round trips, no leaked secrets."""

import json
import io
from datetime import date, timedelta

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
    setup_resp = test_client.post(
        "/setup",
        data={
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "admin-password",
        },
        follow_redirects=False,
    )
    assert setup_resp.status_code == 303
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def unauth_client(temp_db_path):
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


async def _insert_transaction(db, **overrides):
    if "owner_user_id" not in overrides:
        cursor = await db.execute("SELECT id FROM users ORDER BY id LIMIT 1")
        owner = await cursor.fetchone()
        await cursor.close()
        overrides["owner_user_id"] = owner["id"] if owner else None
    fields = dict(
        owner_user_id=None,
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
            owner_user_id, transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (:owner_user_id, :transaction_type, :direction, :status, :occurred_at, :amount, :fee,
            :available_balance, :counterparty, :description, :category, :category_source,
            :parser_version, :parse_status, :parse_confidence, :warnings_json,
            :raw_fields_json, :gmail_message_id)
        """,
        fields,
    )
    await db.commit()
    return cursor.lastrowid


async def _insert_unknown(db, **overrides):
    if "owner_user_id" not in overrides:
        cursor = await db.execute("SELECT id FROM users ORDER BY id LIMIT 1")
        owner = await cursor.fetchone()
        await cursor.close()
        overrides["owner_user_id"] = owner["id"] if owner else None
    fields = dict(
        owner_user_id=None,
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
            owner_user_id, subject, sender, transaction_code, amount, warnings_json,
            raw_fields_json, parser_version, status, gmail_message_id
        ) VALUES (:owner_user_id, :subject, :sender, :transaction_code, :amount, :warnings_json,
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


@pytest.mark.asyncio
async def test_dashboard_shows_expense_summary_windows(client, db_connection):
    today = date.today()
    await _insert_transaction(
        db_connection,
        gmail_message_id="msg-expense-today",
        occurred_at=f"{today.isoformat()}T10:00:00",
        amount=100.0,
    )
    await _insert_transaction(
        db_connection,
        gmail_message_id="msg-expense-10",
        occurred_at=f"{(today - timedelta(days=10)).isoformat()}T10:00:00",
        amount=200.0,
    )
    await _insert_transaction(
        db_connection,
        gmail_message_id="msg-expense-20",
        occurred_at=f"{(today - timedelta(days=20)).isoformat()}T10:00:00",
        amount=300.0,
    )
    await _insert_transaction(
        db_connection,
        gmail_message_id="msg-ignored",
        occurred_at=f"{today.isoformat()}T10:00:00",
        amount=999.0,
        parse_status="ignored",
    )
    await _insert_transaction(
        db_connection,
        gmail_message_id="msg-income",
        occurred_at=f"{today.isoformat()}T10:00:00",
        amount=999.0,
        direction="in",
    )

    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.text
    assert "Expense 7 Days" in body
    assert "Expense 14 Days" in body
    assert "Expense 30 Days" in body
    assert "฿100.00" in body
    assert "฿300.00" in body
    assert "฿600.00" in body


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "2.0.0"}


# ---- Authentication --------------------------------------------------------------


def test_first_visit_without_users_redirects_to_setup(unauth_client):
    resp = unauth_client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"


def test_unauthenticated_user_redirects_to_login_after_setup(unauth_client):
    setup_resp = unauth_client.post(
        "/setup",
        data={
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "admin-password",
        },
        follow_redirects=False,
    )
    assert setup_resp.status_code == 303

    logout_resp = unauth_client.post("/logout", follow_redirects=False)
    assert logout_resp.status_code == 303

    resp = unauth_client.get("/transactions", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login?next=")


def test_unauthenticated_api_returns_401_after_setup(unauth_client):
    unauth_client.post(
        "/setup",
        data={
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "admin-password",
        },
        follow_redirects=False,
    )
    unauth_client.post("/logout", follow_redirects=False)

    resp = unauth_client.get("/api/transactions")

    assert resp.status_code == 401


def test_admin_can_manage_users(client):
    create_resp = client.post(
        "/api/users",
        json={
            "email": "analyst@example.com",
            "display_name": "Analyst",
            "password": "analyst-password",
            "role": "user",
            "is_active": True,
        },
    )
    assert create_resp.status_code == 201
    user = create_resp.json()

    list_resp = client.get("/api/users")
    assert list_resp.status_code == 200
    assert any(item["email"] == "analyst@example.com" for item in list_resp.json()["items"])

    update_resp = client.patch(
        f"/api/users/{user['id']}",
        json={"display_name": "Senior Analyst", "role": "admin", "is_active": True},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["role"] == "admin"

    password_resp = client.post(f"/api/users/{user['id']}/password", json={"password": "new-password"})
    assert password_resp.status_code == 200


def test_non_admin_cannot_access_user_management(client):
    create_resp = client.post(
        "/api/users",
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "viewer-password",
            "role": "user",
            "is_active": True,
        },
    )
    assert create_resp.status_code == 201

    viewer_client = TestClient(app)
    login_resp = viewer_client.post(
        "/login",
        data={"email": "viewer@example.com", "password": "viewer-password", "next": "/"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 303

    resp = viewer_client.get("/api/users")
    assert resp.status_code == 403


def test_cannot_disable_last_active_admin(client):
    users_resp = client.get("/api/users")
    admin = next(item for item in users_resp.json()["items"] if item["email"] == "admin@example.com")

    resp = client.patch(
        f"/api/users/{admin['id']}",
        json={"display_name": "Admin", "role": "user", "is_active": True},
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_runtime_data_is_scoped_per_user(client, db_connection):
    user_resp = client.post(
        "/api/users",
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "viewer-password",
            "role": "user",
            "is_active": True,
        },
    )
    viewer_id = user_resp.json()["id"]

    admin_tx_id = await _insert_transaction(db_connection, gmail_message_id="msg-admin")
    viewer_tx_id = await _insert_transaction(
        db_connection,
        owner_user_id=viewer_id,
        gmail_message_id="msg-viewer",
        counterparty="Viewer Shop",
    )

    admin_list = client.get("/api/transactions").json()
    assert [item["id"] for item in admin_list["items"]] == [admin_tx_id]
    assert client.get(f"/api/transactions/{viewer_tx_id}").status_code == 404

    viewer_client = TestClient(app)
    login_resp = viewer_client.post(
        "/login",
        data={"email": "viewer@example.com", "password": "viewer-password", "next": "/"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 303

    viewer_list = viewer_client.get("/api/transactions").json()
    assert [item["id"] for item in viewer_list["items"]] == [viewer_tx_id]
    assert viewer_client.get(f"/api/transactions/{admin_tx_id}").status_code == 404


def test_mappings_are_scoped_per_user(client):
    user_resp = client.post(
        "/api/users",
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "viewer-password",
            "role": "user",
            "is_active": True,
        },
    )
    assert user_resp.status_code == 201

    admin_mapping = client.post(
        "/api/mappings",
        json={"counterparty": "Netflix", "category": "Entertainment"},
    ).json()

    viewer_client = TestClient(app)
    viewer_client.post(
        "/login",
        data={"email": "viewer@example.com", "password": "viewer-password", "next": "/"},
        follow_redirects=False,
    )
    viewer_mapping = viewer_client.post(
        "/api/mappings",
        json={"counterparty": "Netflix", "category": "Subscriptions"},
    ).json()

    assert admin_mapping["id"] != viewer_mapping["id"]
    assert client.get("/api/mappings").json()["items"][0]["category"] == "Entertainment"
    assert viewer_client.get("/api/mappings").json()["items"][0]["category"] == "Subscriptions"


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
        "gmail_connected",
    }


def test_gmail_status_is_per_user(client):
    user_resp = client.post(
        "/api/users",
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "viewer-password",
            "role": "user",
            "is_active": True,
        },
    )
    assert user_resp.status_code == 201

    resp = client.get("/api/gmail/status")
    assert resp.status_code == 200
    assert resp.json() == {"connected": False}

    viewer_client = TestClient(app)
    viewer_client.post(
        "/login",
        data={"email": "viewer@example.com", "password": "viewer-password", "next": "/"},
        follow_redirects=False,
    )
    viewer_resp = viewer_client.get("/api/gmail/status")
    assert viewer_resp.status_code == 200
    assert viewer_resp.json() == {"connected": False}


def test_settings_page_loads(client):
    resp = client.get("/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_settings_clear_export_import_data(client, db_connection):
    await _insert_transaction(db_connection, gmail_message_id="msg-export")
    cursor = await db_connection.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    owner = await cursor.fetchone()
    await cursor.close()
    await db_connection.execute(
        "INSERT INTO ignored_subjects (owner_user_id, subject, reason) VALUES (?, ?, ?)",
        (owner["id"], "Ignored Export Subject", "test"),
    )
    await db_connection.commit()

    export_resp = client.get("/api/settings/export")
    assert export_resp.status_code == 200
    payload = export_resp.json()
    assert len(payload["tables"]["transactions"]) == 1
    assert len(payload["tables"]["ignored_subjects"]) == 1

    clear_resp = client.post("/api/settings/clear-data")
    assert clear_resp.status_code == 200

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM transactions")
    assert (await cursor.fetchone())["n"] == 0
    await cursor.close()

    import_resp = client.post(
        "/api/settings/import",
        files={"file": ("export.json", io.BytesIO(json.dumps(payload).encode("utf-8")), "application/json")},
    )
    assert import_resp.status_code == 200

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM transactions")
    assert (await cursor.fetchone())["n"] == 1
    await cursor.close()
    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM ignored_subjects")
    assert (await cursor.fetchone())["n"] == 1
    await cursor.close()


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


@pytest.mark.asyncio
async def test_mappings_page_offers_transaction_counterparty_and_category_options(
    client, db_connection
):
    await _insert_transaction(
        db_connection,
        counterparty="Cafe Amazon",
        category="Coffee",
        gmail_message_id="msg-map-options",
    )

    resp = client.get("/mappings")

    assert resp.status_code == 200
    assert 'id="counterparties"' in resp.text
    assert 'value="Cafe Amazon"' in resp.text
    assert 'value="Coffee"' in resp.text


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


@pytest.mark.asyncio
async def test_update_transaction_ignore_flag_form_encoded(client, db_connection):
    """htmx's hx-vals sends booleans as form-encoded strings, not JSON - the form branch
    of update_transaction must also honor `ignore` (this was previously silently ignored,
    making the Ignore/Unignore buttons on the transaction detail page no-ops)."""
    tx_id = await _insert_transaction(db_connection)

    resp = client.patch(f"/api/transactions/{tx_id}", data={"ignore": "true"})
    assert resp.status_code == 200

    cursor = await db_connection.execute("SELECT parse_status FROM transactions WHERE id = ?", (tx_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["parse_status"] == "ignored"


@pytest.mark.asyncio
async def test_update_transaction_htmx_target_transaction_actions_returns_full_card(client, db_connection):
    """When the Save Category button inside transaction_actions.html (hx-target=#transaction-actions)
    submits, the response must be the full actions card, not the bare category badge fragment used
    by the transactions-list inline editor - otherwise Save Category wipes out the Ignore/Reparse/
    Delete buttons until the page is reloaded."""
    tx_id = await _insert_transaction(db_connection)

    resp = client.patch(
        f"/api/transactions/{tx_id}",
        data={"category": "New Category"},
        headers={"HX-Request": "true", "HX-Target": "transaction-actions"},
    )
    assert resp.status_code == 200
    assert "Reparse" in resp.text
    assert "Delete" in resp.text
    assert 'id="transaction-actions"' in resp.text


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


@pytest.mark.asyncio
async def test_transaction_rows_link_to_detail(client, db_connection):
    tx_id = await _insert_transaction(db_connection)

    resp = client.get("/transactions")

    assert resp.status_code == 200
    assert f'data-href="/transactions/{tx_id}"' in resp.text
    assert f'hx-get="/transactions/{tx_id}/edit-category"' in resp.text


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
async def test_unknown_ignore_subject_adds_future_ingestion_filter(client, db_connection):
    unknown_id = await _insert_unknown(db_connection, subject="Bank Marketing Notice")

    resp = client.post(f"/api/unknown/{unknown_id}/ignore-subject")

    assert resp.status_code == 200
    cursor = await db_connection.execute(
        "SELECT subject FROM ignored_subjects WHERE subject = ?",
        ("Bank Marketing Notice",),
    )
    ignored = await cursor.fetchone()
    await cursor.close()
    cursor = await db_connection.execute(
        "SELECT status FROM unknown_patterns WHERE id = ?",
        (unknown_id,),
    )
    unknown = await cursor.fetchone()
    await cursor.close()

    assert ignored["subject"] == "Bank Marketing Notice"
    assert unknown["status"] == "ignored"


@pytest.mark.asyncio
async def test_unknown_page_shows_ignored_subjects(client, db_connection):
    cursor = await db_connection.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    owner = await cursor.fetchone()
    await cursor.close()
    await db_connection.execute(
        "INSERT INTO ignored_subjects (owner_user_id, subject, reason) VALUES (?, ?, ?)",
        (owner["id"], "Ignored Subject", "test"),
    )
    await db_connection.commit()

    resp = client.get("/unknown")

    assert resp.status_code == 200
    assert "Ignored Subjects" in resp.text
    assert "Ignored Subject" in resp.text


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
    captured = {}

    async def fake_run_ingestion(query, reader=None, engine=None, owner_user_id=None):
        captured["owner_user_id"] = owner_user_id
        captured["reader"] = reader
        return {"emails_checked": 3, "inserted": 2, "duplicates": 1, "failed": 0}

    monkeypatch.setattr(ingestion_routes, "run_ingestion", fake_run_ingestion)

    resp = client.post("/api/ingestion/run")
    assert resp.status_code == 200
    assert resp.json() == {"emails_checked": 3, "inserted": 2, "duplicates": 1, "failed": 0}
    assert captured["owner_user_id"] == 1
    assert captured["reader"] is not None


def test_trigger_ingestion_run_accepts_window(client, monkeypatch):
    captured = {}

    async def fake_run_ingestion(query, reader=None, engine=None, owner_user_id=None):
        captured["query"] = query
        captured["owner_user_id"] = owner_user_id
        return {"emails_checked": 0, "inserted": 0, "duplicates": 0, "failed": 0}

    monkeypatch.setattr(ingestion_routes, "run_ingestion", fake_run_ingestion)

    resp = client.post("/api/ingestion/run", data={"window": "last_30_days"})

    assert resp.status_code == 200
    assert "newer_than:30d" in captured["query"]
    assert "newer_than:2d" not in captured["query"]
    assert captured["owner_user_id"] == 1


def test_trigger_ingestion_run_returns_409_when_already_running(client, monkeypatch):
    async def fake_run_ingestion(query, reader=None, engine=None, owner_user_id=None):
        raise ingestion_routes.IngestionAlreadyRunningError("An ingestion run is already in progress")

    monkeypatch.setattr(ingestion_routes, "run_ingestion", fake_run_ingestion)

    resp = client.post("/api/ingestion/run")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_runs_empty_then_populated(client, db_connection):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    cursor = await db_connection.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    owner = await cursor.fetchone()
    await cursor.close()
    await db_connection.execute(
        """
        INSERT INTO ingestion_runs (owner_user_id, emails_checked, inserted, duplicates, failed, duration_seconds)
        VALUES (?, 1, 1, 0, 0, 0.5)
        """,
        (owner["id"],),
    )
    await db_connection.commit()

    resp = client.get("/api/runs")
    assert resp.json()["total"] == 1


def test_retry_run_not_found(client):
    resp = client.post("/api/ingestion/retry/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_run_retries_pending_unknowns(client, db_connection, monkeypatch):
    cursor = await db_connection.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    owner = await cursor.fetchone()
    await cursor.close()
    cursor = await db_connection.execute(
        """
        INSERT INTO ingestion_runs (owner_user_id, emails_checked, inserted, duplicates, failed, duration_seconds)
        VALUES (?, 1, 0, 0, 1, 0.1)
        """,
        (owner["id"],),
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
