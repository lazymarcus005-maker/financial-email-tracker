"""Tests for app.ingestion.reparse - re-running the parser against an existing email."""

from datetime import datetime

import pytest

from app.gmail import EmailMessage
from app.ingestion import persistence, reparse
from app.parsers.base import Transaction
from app.parsers.registry import ParserRegistry


def _message(message_id="msg-unknown-1", sender="notify@kasikornbank.com"):
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


class _FakeGmailClient:
    def __init__(self, message):
        self._message = message

    def get_message(self, message_id):
        return self._message


class _FakeRegistry:
    def __init__(self, transaction):
        self._transaction = transaction

    def parse(self, email_text, sender, subject=""):
        return self._transaction

    def identify_bank(self, sender):
        return "KBank"


class _FakeEngine:
    async def categorize(self, db, transaction_dict, manual_override=None):
        return "Uncategorized", "uncategorized"


@pytest.mark.asyncio
async def test_reparse_unknown_resolves_instead_of_deleting(db_connection):
    message = _message()
    await persistence.insert_unknown(db_connection, message, None)
    await db_connection.commit()
    cursor = await db_connection.execute(
        "SELECT id FROM unknown_patterns WHERE gmail_message_id = ?", (message.gmail_message_id,)
    )
    unknown_id = (await cursor.fetchone())["id"]
    await cursor.close()

    result = await reparse.reparse_unknown(
        db_connection,
        unknown_id,
        gmail_client=_FakeGmailClient(message),
        registry=_FakeRegistry(_transaction()),
        engine=_FakeEngine(),
    )

    assert result["status"] == "parsed"

    cursor = await db_connection.execute(
        "SELECT u.status AS status, u.resolved_transaction_id AS resolved_transaction_id, t.bank AS bank "
        "FROM unknown_patterns u JOIN transactions t ON t.id = u.resolved_transaction_id WHERE u.id = ?",
        (unknown_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row["status"] == "resolved"
    assert row["resolved_transaction_id"] is not None
    assert row["bank"] == "KBank"

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM unknown_patterns WHERE id = ?", (unknown_id,))
    still_exists = (await cursor.fetchone())["n"]
    await cursor.close()
    assert still_exists == 1


@pytest.mark.asyncio
async def test_reparse_transaction_updates_bank(db_connection):
    message = _message(message_id="msg-existing")
    transaction_id = await persistence.insert_transaction(
        db_connection, message, _transaction(), "Uncategorized", "uncategorized", bank=None
    )
    await db_connection.commit()

    result = await reparse.reparse_transaction(
        db_connection,
        transaction_id,
        gmail_client=_FakeGmailClient(message),
        registry=_FakeRegistry(_transaction()),
        engine=_FakeEngine(),
    )

    assert result["status"] == "parsed"
    cursor = await db_connection.execute("SELECT bank FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    assert row["bank"] == "KBank"
