"""Tests for app.ingestion.service."""

import asyncio
from datetime import datetime

import pytest

from app.gmail import EmailMessage
from app.ingestion.service import run_ingestion
from app.parsers.base import Transaction
from app.storage import database


class FakeReader:
    def __init__(self, messages):
        self._messages = messages
        self.last_query = None

    def read(self, query, max_results=100):
        self.last_query = query
        return self._messages


class FakeRegistry:
    def __init__(self, transaction_by_sender):
        self._transaction_by_sender = transaction_by_sender

    def parse(self, email_text, sender, subject=""):
        return self._transaction_by_sender.get(sender)

    def identify_bank(self, sender):
        return None


def _make_message(message_id, sender="notify@kasikornbank.com"):
    return EmailMessage(
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        sender=sender,
        subject="K PLUS: Transfer Successful",
        received_at=datetime(2025, 1, 26, 14, 32),
        body_text="Transaction Date: 26/01/2025\nAmount: 100.00 THB",
    )


def _make_transaction():
    return Transaction(
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2025-01-26T14:32",
        amount=100.0,
        parse_status="complete",
        parse_confidence=1.0,
        raw_fields={"Amount": "100.00 THB"},
    )


def _make_ignored_notification():
    return Transaction(
        transaction_type="notification",
        direction="unknown",
        status="ignored",
        occurred_at="",
        amount=0.0,
        parse_status="ignored",
        parse_confidence=1.0,
        raw_fields={"ignored_reason": "non_transaction_notification"},
    )


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    asyncio.run(database.init_db())
    return db_path


@pytest.mark.asyncio
async def test_inserts_new_transaction(temp_db):
    message = _make_message("msg-1")
    reader = FakeReader([message])
    registry = FakeRegistry({message.sender: _make_transaction()})

    summary = await run_ingestion("query", reader=reader, registry=registry)

    assert summary == {"emails_checked": 1, "inserted": 1, "duplicates": 0, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute("SELECT gmail_message_id, amount FROM transactions")
    row = await cursor.fetchone()
    await db.close()
    assert row["gmail_message_id"] == "msg-1"
    assert row["amount"] == 100.0


@pytest.mark.asyncio
async def test_dedups_already_ingested_message(temp_db):
    message = _make_message("msg-2")
    reader = FakeReader([message])
    registry = FakeRegistry({message.sender: _make_transaction()})

    await run_ingestion("query", reader=reader, registry=registry)
    summary = await run_ingestion("query", reader=reader, registry=registry)

    assert summary == {"emails_checked": 1, "inserted": 0, "duplicates": 1, "failed": 0}


@pytest.mark.asyncio
async def test_logs_unparseable_email_as_unknown(temp_db):
    message = _make_message("msg-3")
    reader = FakeReader([message])
    registry = FakeRegistry({})  # no transaction registered -> parse() returns None

    summary = await run_ingestion("query", reader=reader, registry=registry)

    assert summary == {"emails_checked": 1, "inserted": 0, "duplicates": 0, "failed": 1}

    db = await database.get_connection()
    cursor = await db.execute("SELECT gmail_message_id FROM unknown_patterns")
    row = await cursor.fetchone()
    await db.close()
    assert row["gmail_message_id"] == "msg-3"


@pytest.mark.asyncio
async def test_ingestion_applies_ignored_subjects_to_gmail_query(temp_db):
    message = _make_message("msg-ignored-subject")
    reader = FakeReader([message])
    registry = FakeRegistry({message.sender: _make_transaction()})

    db = await database.get_connection()
    await db.execute(
        "INSERT INTO ignored_subjects (subject, reason) VALUES (?, ?)",
        (message.subject, "test"),
    )
    await db.commit()
    await db.close()

    summary = await run_ingestion(
        "from:(KPLUS@kasikornbank.com) newer_than:7d",
        reader=reader,
        registry=registry,
    )

    assert '-subject:"K PLUS: Transfer Successful"' in reader.last_query
    assert summary == {"emails_checked": 1, "inserted": 0, "duplicates": 0, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute("SELECT COUNT(*) AS n FROM transactions")
    transaction_count = (await cursor.fetchone())["n"]
    await db.close()
    assert transaction_count == 0


@pytest.mark.asyncio
async def test_skips_ignored_parse_without_unknown_pattern(temp_db):
    message = _make_message("msg-ignored", sender="LHBYou@lhbank.co.th")
    message.subject = "[แจ้งเตือน] - การเข้าใช้งานแอปพลิเคชัน / Login Notification."
    reader = FakeReader([message])
    registry = FakeRegistry({message.sender: _make_ignored_notification()})

    summary = await run_ingestion("query", reader=reader, registry=registry)

    assert summary == {"emails_checked": 1, "inserted": 0, "duplicates": 0, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute("SELECT COUNT(*) AS n FROM unknown_patterns")
    unknown_count = (await cursor.fetchone())["n"]
    cursor = await db.execute("SELECT COUNT(*) AS n FROM transactions")
    transaction_count = (await cursor.fetchone())["n"]
    await db.close()

    assert unknown_count == 0
    assert transaction_count == 0


@pytest.mark.asyncio
async def test_successful_ingestion_resolves_existing_unknown(temp_db):
    message = _make_message("msg-unknown-fixed")
    failing_reader = FakeReader([message])
    failing_registry = FakeRegistry({})

    await run_ingestion("query", reader=failing_reader, registry=failing_registry)

    fixed_reader = FakeReader([message])
    fixed_registry = FakeRegistry({message.sender: _make_transaction()})
    summary = await run_ingestion("query", reader=fixed_reader, registry=fixed_registry)

    assert summary == {"emails_checked": 1, "inserted": 1, "duplicates": 0, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute(
        "SELECT status, resolved_transaction_id FROM unknown_patterns WHERE gmail_message_id = ?",
        (message.gmail_message_id,),
    )
    unknown_row = await cursor.fetchone()
    cursor = await db.execute("SELECT id FROM transactions WHERE gmail_message_id = ?", (message.gmail_message_id,))
    transaction_row = await cursor.fetchone()
    await db.close()

    assert unknown_row["status"] == "resolved"
    assert unknown_row["resolved_transaction_id"] == transaction_row["id"]


@pytest.mark.asyncio
async def test_records_ingestion_run(temp_db):
    message = _make_message("msg-4")
    reader = FakeReader([message])
    registry = FakeRegistry({message.sender: _make_transaction()})

    await run_ingestion("query", reader=reader, registry=registry)

    db = await database.get_connection()
    cursor = await db.execute("SELECT emails_checked, inserted, duplicates, failed FROM ingestion_runs")
    row = await cursor.fetchone()
    await db.close()
    assert dict(row) == {"emails_checked": 1, "inserted": 1, "duplicates": 0, "failed": 0}
