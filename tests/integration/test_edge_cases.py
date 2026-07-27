"""Edge cases: missing/malformed fields, unusual amounts, unknown transaction types.

Exercises the KBank parser directly for field-level behavior, and the full
ingestion pipeline for the "unparseable email ends up in unknown_patterns,
not crashing the run" contract.
"""

import pytest

from app.classification.engine import CategoryEngine
from app.ingestion.service import run_ingestion
from app.parsers.kbank.parser import KBankParser
from app.parsers.registry import ParserRegistry
from app.storage import database

SUCCESS_SUBJECT = "K PLUS: You have sent money successfully"


def _email(*lines):
    return "Transfer Successful\n\n" + "\n".join(lines)


# ---- Missing required fields -------------------------------------------------


def test_missing_amount_field_fails_validation():
    email = _email("Transaction Date : 26/01/2025")
    transaction = KBankParser().parse(email, subject=SUCCESS_SUBJECT)

    assert transaction is not None
    assert transaction.parse_status == "failed"
    assert any("amount" in w for w in transaction.parse_warnings)


def test_missing_date_field_fails_validation():
    email = _email("Amount : 100.00 THB")
    transaction = KBankParser().parse(email, subject=SUCCESS_SUBJECT)

    assert transaction is not None
    assert transaction.parse_status == "failed"
    assert any("transaction_date" in w for w in transaction.parse_warnings)


def test_invalid_date_value_is_treated_as_missing():
    email = _email("Transaction Date : not-a-real-date-at-all", "Amount : 100.00 THB")
    transaction = KBankParser().parse(email, subject=SUCCESS_SUBJECT)

    assert transaction.parse_status == "failed"


def test_no_recognizable_fields_returns_none():
    transaction = KBankParser().parse("Just a plain notice with no fields.", subject="Notice")
    assert transaction is None


@pytest.mark.asyncio
async def test_unparseable_email_lands_in_unknown_patterns_not_crash(
    temp_db_path, make_message, fake_reader
):
    message = make_message("msg-edge-missing", _email("Amount : 100.00 THB"))
    reader = fake_reader([message])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary == {"emails_checked": 1, "inserted": 0, "duplicates": 0, "failed": 1}

    db = await database.get_connection()
    cursor = await db.execute("SELECT gmail_message_id FROM unknown_patterns")
    row = await cursor.fetchone()
    await db.close()
    assert row["gmail_message_id"] == "msg-edge-missing"


# ---- Malformed / unusual amounts ---------------------------------------------


@pytest.mark.parametrize(
    "amount_line,expected",
    [
        ("Amount : ฿12,345.67", 12345.67),
        ("Amount : 1234.50", 1234.50),
        ("Amount : 2.5k THB", 2500.0),
        ("Amount : 1.2m THB", 1_200_000.0),
        ("Amount : 0.00 THB", 0.0),
        ("Amount : -500.00 THB", -500.0),
        ("Amount : 9,999,999.99 THB", 9_999_999.99),
    ],
)
def test_amount_variants_parse_to_expected_float(amount_line, expected):
    email = _email("Transaction Date : 26/01/2025", amount_line)
    transaction = KBankParser().parse(email, subject=SUCCESS_SUBJECT)

    assert transaction is not None
    assert transaction.amount == expected
    assert transaction.parse_status == "complete"


def test_negative_amount_refund_ingests_with_correct_sign():
    email = _email("Transaction Date : 26/01/2025", "Amount : -250.00 THB")
    transaction = KBankParser().parse(email, subject=SUCCESS_SUBJECT)
    assert transaction.amount == -250.0


# ---- Unknown transaction type -------------------------------------------------


def test_unknown_transaction_type_still_parses_as_partial():
    # Deliberately avoids the word "Transfer" (used by _email()'s heading) so the
    # body-text fallback keyword scan has nothing to match either.
    email = "Transaction Date : 26/01/2025\nAmount : 100.00 THB"
    transaction = KBankParser().parse(email, subject="K PLUS Notification")

    assert transaction is not None
    assert transaction.transaction_type == "unknown"
    assert transaction.parse_status == "partial"
    assert any("transaction_type" in w for w in transaction.parse_warnings)


# ---- Missing counterparty -----------------------------------------------------


def test_missing_counterparty_falls_back_to_unknown_counterparty():
    email = _email("Transaction Date : 26/01/2025", "Amount : 100.00 THB")
    transaction = KBankParser().parse(email, subject=SUCCESS_SUBJECT)

    assert transaction.counterparty == "Unknown Counterparty"
