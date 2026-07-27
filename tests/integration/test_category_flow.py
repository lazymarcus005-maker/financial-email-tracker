"""Category flow through the full ingestion pipeline: manual override (saved via the
API, then reused via history) > history > rule > AI fallback > uncategorized.
"""

import pytest
from fastapi.testclient import TestClient

from app.classification.engine import CategoryEngine
from app.ingestion.service import run_ingestion
from app.parsers.registry import ParserRegistry
from app.storage import database
from app.web import deps
from app.web.main import app

SUBJECT = "K PLUS: You have sent money successfully"


@pytest.fixture
def client(temp_db_path):
    app.dependency_overrides[deps.get_gmail_client] = lambda: object()
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _email(counterparty, amount="100.00"):
    return "\n".join(
        [
            "Transaction Date : 26/01/2025",
            f"Amount : {amount} THB",
            f"Recipient : {counterparty}",
        ]
    )


@pytest.mark.asyncio
async def test_rule_match_applies_during_ingestion(temp_db_path, make_message, fake_reader):
    message = make_message("msg-cat-rule", _email("Shopee Thailand"), subject=SUBJECT)
    reader = fake_reader([message])

    await run_ingestion("query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine())

    db = await database.get_connection()
    cursor = await db.execute("SELECT category, category_source FROM transactions WHERE gmail_message_id = ?", ("msg-cat-rule",))
    row = await cursor.fetchone()
    await db.close()
    assert row["category"] == "Shopping"
    assert row["category_source"] == "rule"


@pytest.mark.asyncio
async def test_uncategorized_when_nothing_matches(temp_db_path, make_message, fake_reader):
    message = make_message("msg-cat-none", _email("Some Random Merchant"), subject=SUBJECT)
    reader = fake_reader([message])

    await run_ingestion("query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine())

    db = await database.get_connection()
    cursor = await db.execute("SELECT category, category_source FROM transactions WHERE gmail_message_id = ?", ("msg-cat-none",))
    row = await cursor.fetchone()
    await db.close()
    assert row["category"] == "Uncategorized"
    assert row["category_source"] == "uncategorized"


@pytest.mark.asyncio
async def test_ai_fallback_used_when_enabled_and_no_rule_or_history(
    temp_db_path, make_message, fake_reader, monkeypatch
):
    from app.classification import ai as ai_module

    async def fake_categorize(transaction, base_url, model, timeout=10.0):
        return "Food"

    monkeypatch.setattr(ai_module, "categorize", fake_categorize)

    message = make_message("msg-cat-ai", _email("Some Random Merchant"), subject=SUBJECT)
    reader = fake_reader([message])

    await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine(ai_enabled=True, ollama_model="qwen3:1.7b")
    )

    db = await database.get_connection()
    cursor = await db.execute("SELECT category, category_source FROM transactions WHERE gmail_message_id = ?", ("msg-cat-ai",))
    row = await cursor.fetchone()
    await db.close()
    assert row["category"] == "Food"
    assert row["category_source"] == "qwen3:1.7b"


def test_manual_override_saves_via_api_and_is_reused_via_history(client, make_message, fake_reader):
    import asyncio

    message = make_message("msg-cat-manual-1", _email("Uniquely Named Merchant Co"), subject=SUBJECT)

    async def _ingest_first():
        return await run_ingestion(
            "query", reader=fake_reader([message]), registry=ParserRegistry(), engine=CategoryEngine()
        )

    summary = asyncio.run(_ingest_first())
    assert summary["inserted"] == 1

    listing = client.get("/api/transactions").json()
    txn = next(t for t in listing["items"] if t["gmail_message_id"] == "msg-cat-manual-1")
    assert txn["category"] == "Uncategorized"

    patch_resp = client.patch(f"/api/transactions/{txn['id']}", json={"category": "Side Business"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["category"] == "Side Business"
    assert patch_resp.json()["category_source"] == "manual"

    # A second transaction from the same counterparty (different amount, so it
    # isn't fingerprint-deduped against the first) should pick up the
    # manually-taught category via history, without any further override.
    message_2 = make_message(
        "msg-cat-manual-2", _email("Uniquely Named Merchant Co", amount="250.00"), subject=SUBJECT
    )

    async def _ingest_second():
        return await run_ingestion(
            "query", reader=fake_reader([message_2]), registry=ParserRegistry(), engine=CategoryEngine()
        )

    asyncio.run(_ingest_second())

    listing_2 = client.get("/api/transactions").json()
    txn_2 = next(t for t in listing_2["items"] if t["gmail_message_id"] == "msg-cat-manual-2")
    assert txn_2["category"] == "Side Business"
    assert txn_2["category_source"] == "history"
