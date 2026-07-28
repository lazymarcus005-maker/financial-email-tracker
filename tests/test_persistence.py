"""Tests for app.ingestion.persistence - DB writes for transactions and unknown patterns."""

from datetime import datetime

import pytest

from app.gmail import EmailMessage
from app.ingestion import persistence
from app.parsers.base import Transaction


def _message(message_id="msg-1", sender="notify@kasikornbank.com"):
    return EmailMessage(
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        sender=sender,
        subject="K PLUS: Transfer Successful",
        received_at=datetime(2025, 1, 26, 14, 32),
        body_text="Transaction Date: 26/01/2025\nAmount: 100.00 THB",
    )


def _transaction():
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


@pytest.mark.asyncio
async def test_insert_transaction_stores_bank_and_returns_id(db_connection):
    transaction_id = await persistence.insert_transaction(
        db_connection, _message(), _transaction(), "Shopping", "rule", bank="KBank"
    )
    await db_connection.commit()

    assert isinstance(transaction_id, int)
    cursor = await db_connection.execute("SELECT bank FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["bank"] == "KBank"


@pytest.mark.asyncio
async def test_insert_unknown_stores_received_at(db_connection):
    await persistence.insert_unknown(db_connection, _message(), None)
    await db_connection.commit()

    cursor = await db_connection.execute("SELECT received_at FROM unknown_patterns WHERE gmail_message_id = ?", ("msg-1",))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["received_at"] == "2025-01-26T14:32:00"


@pytest.mark.asyncio
async def test_resolve_unknown_marks_resolved_without_deleting(db_connection):
    await persistence.insert_unknown(db_connection, _message(), None)
    await db_connection.commit()
    cursor = await db_connection.execute("SELECT id FROM unknown_patterns WHERE gmail_message_id = ?", ("msg-1",))
    unknown_id = (await cursor.fetchone())["id"]
    await cursor.close()

    await persistence.resolve_unknown(db_connection, unknown_id, 42)
    await db_connection.commit()

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id, resolved_at FROM unknown_patterns WHERE id = ?", (unknown_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] == 42
    assert row["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_unknown_by_message_only_updates_pending_rows(db_connection):
    await persistence.insert_unknown(db_connection, _message(), None)
    await db_connection.commit()

    await persistence.resolve_unknown_by_message(db_connection, "msg-1", 42)
    await db_connection.commit()

    cursor = await db_connection.execute(
        "SELECT status, resolved_transaction_id FROM unknown_patterns WHERE gmail_message_id = ?", ("msg-1",)
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] == 42


@pytest.mark.asyncio
async def test_insert_manual_transaction_creates_transaction_with_manual_source(db_connection):
    transaction_id = await persistence.insert_manual_transaction(
        db_connection,
        gmail_message_id="msg-unknown-1",
        bank="SCB",
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at="2026-07-27T10:00:00",
        amount=250.0,
        category="Shopping",
        fee=1.5,
        available_balance=1000.0,
        counterparty="Shopee",
        description="Manual entry",
    )
    await db_connection.commit()

    cursor = await db_connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["category_source"] == "manual"
    assert row["bank"] == "SCB"
    assert row["amount"] == 250.0
    assert row["gmail_message_id"] == "msg-unknown-1"
