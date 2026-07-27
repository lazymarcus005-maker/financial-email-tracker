"""Integration: an English-only KBank email (no Thai section at all) still parses correctly."""

import json

import pytest

from app.classification.engine import CategoryEngine
from app.ingestion.service import run_ingestion
from app.parsers.kbank.detector import detect_section
from app.parsers.kbank.parser import KBankParser
from app.parsers.registry import ParserRegistry
from app.storage import database


def test_english_section_used_when_no_thai_present(english_transfer_email):
    result = detect_section(english_transfer_email)
    assert result.language == "en"
    assert "Transaction Date" in result.section_text


def test_logs_thai_section_not_found_warning(english_transfer_email, caplog):
    with caplog.at_level("DEBUG"):
        detect_section(english_transfer_email)

    events = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert any(e.get("event") == "thai_section_not_found" for e in events)


def test_english_only_email_parses_completely(english_transfer_email):
    transaction = KBankParser().parse(
        english_transfer_email, subject="K PLUS: You have sent money successfully"
    )

    assert transaction is not None
    assert transaction.parse_status == "complete"
    assert transaction.amount == 1500.00
    assert transaction.transaction_type == "bank_transfer"


@pytest.mark.asyncio
async def test_english_only_email_ingests_end_to_end(
    temp_db_path, make_message, fake_reader, english_transfer_email
):
    message = make_message("msg-en-1", english_transfer_email)
    reader = fake_reader([message])

    summary = await run_ingestion(
        "query", reader=reader, registry=ParserRegistry(), engine=CategoryEngine()
    )

    assert summary["inserted"] == 1

    db = await database.get_connection()
    cursor = await db.execute(
        "SELECT parse_status FROM transactions WHERE gmail_message_id = ?", ("msg-en-1",)
    )
    row = await cursor.fetchone()
    await db.close()
    assert row["parse_status"] == "complete"
