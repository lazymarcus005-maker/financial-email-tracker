"""End-to-end: Gmail -> ParserRegistry -> dedup -> CategoryEngine -> SQLite, using real components."""

import pytest

from app.classification.engine import CategoryEngine
from app.ingestion.service import run_ingestion
from app.parsers.registry import ParserRegistry
from app.storage import database


@pytest.mark.asyncio
async def test_kbank_email_is_parsed_categorized_and_persisted(
    temp_db_path, make_message, fake_reader, english_transfer_email
):
    message = make_message("msg-e2e-1", english_transfer_email)
    reader = fake_reader([message])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary == {"emails_checked": 1, "inserted": 1, "duplicates": 0, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute(
        "SELECT * FROM transactions WHERE gmail_message_id = ?", ("msg-e2e-1",)
    )
    row = await cursor.fetchone()
    await db.close()

    assert row is not None
    assert row["transaction_type"] == "bank_transfer"
    assert row["direction"] == "out"
    assert row["status"] == "success"
    assert row["amount"] == 1500.00
    assert row["available_balance"] == 25430.50
    assert row["transaction_id"] == "202501261432001234"
    assert row["parse_status"] == "complete"
    assert row["category"] == "Uncategorized"
    assert row["category_source"] == "uncategorized"


@pytest.mark.asyncio
async def test_retrying_the_same_email_does_not_duplicate(
    temp_db_path, make_message, fake_reader, english_transfer_email
):
    message = make_message("msg-e2e-2", english_transfer_email)
    reader = fake_reader([message])
    registry = ParserRegistry()
    engine = CategoryEngine()

    first = await run_ingestion("query", reader=reader, registry=registry, engine=engine)
    second = await run_ingestion("query", reader=reader, registry=registry, engine=engine)

    assert first["inserted"] == 1
    assert second == {"emails_checked": 1, "inserted": 0, "duplicates": 1, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute("SELECT COUNT(*) AS n FROM transactions")
    row = await cursor.fetchone()
    await db.close()
    assert row["n"] == 1


@pytest.mark.asyncio
async def test_ingestion_run_is_recorded(temp_db_path, make_message, fake_reader, english_transfer_email):
    message = make_message("msg-e2e-3", english_transfer_email)
    reader = fake_reader([message])

    await run_ingestion("query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine())

    db = await database.get_connection()
    cursor = await db.execute("SELECT emails_checked, inserted, duplicates, failed FROM ingestion_runs")
    row = await cursor.fetchone()
    await db.close()
    assert dict(row) == {"emails_checked": 1, "inserted": 1, "duplicates": 0, "failed": 0}
