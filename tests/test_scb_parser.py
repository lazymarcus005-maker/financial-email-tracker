"""Integration tests for the full SCB parser pipeline."""

import re
from pathlib import Path

from app.parsers.scb.mapper import _parse_amount, _parse_occurred_at
from app.parsers.scb.parser import SCBParser

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scb_samples.txt"


def _load_samples() -> dict[int, str]:
    """Split the fixture file into a {sample_number: email_text} mapping."""
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    parts = re.split(r"=== SCB Email Sample (\d+) ===\n", text)
    # parts[0] is the preamble (empty); then alternating number, body.
    samples = {}
    for i in range(1, len(parts), 2):
        samples[int(parts[i])] = parts[i + 1].strip("\n")
    return samples


SAMPLES = _load_samples()


def test_parses_sample_1_bank_transfer():
    parser = SCBParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.amount == 10.00
    assert transaction.fee == 0.0
    assert transaction.transaction_type == "bank_transfer"
    assert transaction.direction == "out"
    assert transaction.status == "success"
    assert transaction.occurred_at == "2026-07-28T07:16:35"
    assert transaction.parse_status == "complete"


def test_can_handle_scb_sender():
    parser = SCBParser()
    assert parser.can_handle("scbeasynet@scb.co.th")
    assert parser.can_handle("noreply@scb.co.th")


def test_cannot_handle_kbank_sender():
    parser = SCBParser()
    assert not parser.can_handle("KPLUS@kasikornbank.com")


def test_date_parsing_thai_month_buddhist_year():
    assert _parse_occurred_at("28 ก.ค. 2569 ณ 07:16:35") == "2026-07-28T07:16:35"


def test_amount_parsing_baht_suffix():
    assert _parse_amount("10.00 บาท") == 10.0


def test_from_to_bank_and_account_extraction():
    parser = SCBParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.raw_fields["from_bank"] == "ธนาคารไทยพาณิชย์"
    assert transaction.raw_fields["from_account"] == "xxxxxx8161"
    assert transaction.raw_fields["to_bank"] == "ธนาคารKBank"
    assert transaction.raw_fields["to_account"] == "0148929335"
    assert transaction.counterparty == "ธนาคารKBank 0148929335"


def test_all_raw_fields_captured():
    parser = SCBParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    expected = {
        "transaction_type_label",
        "from_bank",
        "from_account",
        "to_bank",
        "to_account",
        "amount",
        "occurred_at",
        "details",
    }
    assert expected.issubset(transaction.raw_fields.keys())
    assert transaction.raw_fields["transaction_type_label"] == "โอนเงินไปธนาคารอื่น"
    assert transaction.raw_fields["amount"] == "10.00 บาท"
    assert transaction.raw_fields["occurred_at"] == "28 ก.ค. 2569 ณ 07:16:35"
