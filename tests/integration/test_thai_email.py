"""Integration: a Thai-language KBank email through the full detector -> extractor -> mapper -> parser pipeline."""

import pytest

from app.classification.engine import CategoryEngine
from app.ingestion.service import run_ingestion
from app.parsers.kbank.detector import detect_section
from app.parsers.kbank.parser import KBankParser
from app.parsers.registry import ParserRegistry
from app.storage import database


def test_thai_section_is_detected(thai_transfer_email):
    result = detect_section(thai_transfer_email)
    assert result.language == "th"
    assert "วันที่ทำรายการ" in result.section_text


def test_thai_fields_are_extracted_and_parsed(thai_transfer_email):
    transaction = KBankParser().parse(thai_transfer_email, subject="K PLUS: You have sent money successfully")

    assert transaction is not None
    assert transaction.amount == 1500.00
    assert transaction.fee == 0.00
    assert transaction.available_balance == 25430.50
    assert transaction.occurred_at == "2025-01-26T14:32"
    assert transaction.transaction_id == "202501261432001234"
    assert transaction.parse_status == "complete"
    # Raw fields preserve the original Thai labels.
    assert "จำนวนเงิน" in transaction.raw_fields
    assert "วันที่ทำรายการ" in transaction.raw_fields


@pytest.mark.asyncio
async def test_thai_email_ingests_end_to_end(temp_db_path, make_message, fake_reader, thai_transfer_email):
    message = make_message("msg-thai-1", thai_transfer_email)
    reader = fake_reader([message])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary["inserted"] == 1

    db = await database.get_connection()
    cursor = await db.execute(
        "SELECT amount, available_balance, parse_status FROM transactions WHERE gmail_message_id = ?",
        ("msg-thai-1",),
    )
    row = await cursor.fetchone()
    await db.close()
    assert row["amount"] == 1500.00
    assert row["available_balance"] == 25430.50
    assert row["parse_status"] == "complete"
