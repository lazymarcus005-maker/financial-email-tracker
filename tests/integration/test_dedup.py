"""Deduplication: same gmail_message_id, same transaction reference, and same
fingerprint (type/direction/amount/occurred_at/counterparty) must all collapse
to a single row - whether the retry lands in the same ingestion run or a later one.
"""

import pytest

from app.classification.engine import CategoryEngine
from app.ingestion.service import run_ingestion
from app.parsers.registry import ParserRegistry
from app.storage import database

SUBJECT = "K PLUS: You have sent money successfully"


def _email(*lines):
    return "\n".join(lines)


@pytest.mark.asyncio
async def test_same_gmail_message_id_is_skipped_on_retry(
    temp_db_path, make_message, fake_reader, english_transfer_email
):
    message = make_message("msg-dup-1", english_transfer_email)
    reader = fake_reader([message])
    registry, engine = ParserRegistry(), CategoryEngine()

    first = await run_ingestion("query", reader=reader, registry=registry, engine=engine)
    second = await run_ingestion("query", reader=reader, registry=registry, engine=engine)

    assert first["inserted"] == 1
    assert second == {"emails_checked": 1, "inserted": 0, "duplicates": 1, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute("SELECT COUNT(*) AS n FROM transactions")
    n = (await cursor.fetchone())["n"]
    await db.close()
    assert n == 1


@pytest.mark.asyncio
async def test_same_reference_number_under_different_message_id_is_deduped(
    temp_db_path, make_message, fake_reader
):
    # Same bank reference number (a resend/forward), different gmail_message_id.
    body = _email(
        "Transaction Date : 26/01/2025",
        "Amount : 1,500.00 THB",
        "Reference No : REF-SAME-001",
    )
    message_a = make_message("msg-dup-a", body, subject=SUBJECT)
    message_b = make_message("msg-dup-b", body, subject=SUBJECT)
    reader = fake_reader([message_a, message_b])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary == {"emails_checked": 2, "inserted": 1, "duplicates": 1, "failed": 0}

    db = await database.get_connection()
    cursor = await db.execute("SELECT COUNT(*) AS n FROM transactions WHERE transaction_id = ?", ("REF-SAME-001",))
    n = (await cursor.fetchone())["n"]
    await db.close()
    assert n == 1


@pytest.mark.asyncio
async def test_same_fingerprint_without_reference_number_is_deduped(
    temp_db_path, make_message, fake_reader
):
    # No reference number at all - dedup must fall back to the
    # (type, direction, amount, occurred_at, counterparty) fingerprint.
    body = _email("Transaction Date : 26/01/2025", "Transaction Time : 14:32", "Amount : 750.00 THB")
    message_a = make_message("msg-fp-a", body, subject=SUBJECT)
    message_b = make_message("msg-fp-b", body, subject=SUBJECT)
    reader = fake_reader([message_a, message_b])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary == {"emails_checked": 2, "inserted": 1, "duplicates": 1, "failed": 0}


@pytest.mark.asyncio
async def test_different_amount_is_not_deduped(temp_db_path, make_message, fake_reader):
    body_a = _email("Transaction Date : 26/01/2025", "Amount : 100.00 THB")
    body_b = _email("Transaction Date : 26/01/2025", "Amount : 200.00 THB")
    message_a = make_message("msg-diff-a", body_a, subject=SUBJECT)
    message_b = make_message("msg-diff-b", body_b, subject=SUBJECT)
    reader = fake_reader([message_a, message_b])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary == {"emails_checked": 2, "inserted": 2, "duplicates": 0, "failed": 0}
