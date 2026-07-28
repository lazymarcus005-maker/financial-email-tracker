"""Integration tests for the full LH Bank parser pipeline."""

import re
from pathlib import Path

from app.parsers.lhbank.mapper import _parse_occurred_at
from app.parsers.lhbank.parser import LHBankParser

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lhbank_samples.txt"


def _load_samples() -> dict[int, str]:
    """Split the fixture file into a {sample_number: email_text} mapping."""
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    parts = re.split(r"=== LH Bank Sample (\d+) ===\n", text)
    # parts[0] is the preamble (empty); then alternating number, body.
    samples = {}
    for i in range(1, len(parts), 2):
        samples[int(parts[i])] = parts[i + 1].strip("\n")
    return samples


SAMPLES = _load_samples()


def test_parses_sample_1_bill_payment():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.amount == 256.25
    assert transaction.fee == 0.0
    assert transaction.transaction_type == "bill_payment"
    assert transaction.direction == "out"
    assert transaction.status == "success"
    assert transaction.parse_status == "complete"


def test_can_handle_lhbank_sender():
    parser = LHBankParser()
    assert parser.can_handle("LHBYou@lhbank.co.th")
    assert parser.can_handle("noreply@lhbank.co.th")


def test_cannot_handle_kbank_sender():
    parser = LHBankParser()
    assert not parser.can_handle("KPLUS@kasikornbank.com")


def test_date_parsing():
    assert _parse_occurred_at("วันอาทิตย์, 26 ก.ค. 2569 12:15") == "2026-07-26T12:15:00"


def test_occurred_at_from_sample():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.occurred_at == "2026-07-26T12:15:00"


def test_counterparty_extraction():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    # The truncated "(HEAD" parenthetical is stripped from the raw value.
    assert transaction.counterparty == "CP AXTRA PUBLIC COMPANY LIMITED"


def test_merchant_code_1_extraction():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.raw_fields["merchant_code_1"] == "000002205808025"


def test_reference_2_extraction():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.raw_fields["reference_2"] == "460916606S4FPA000000"
    assert transaction.transaction_id == "460916606S4FPA000000"


def test_memo_captured_even_when_empty():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert "memo" in transaction.raw_fields
    assert transaction.raw_fields["memo"] == ""


def test_device_field():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.raw_fields["device"] == "iPhone 15 Pro"


def test_all_raw_fields_captured():
    parser = LHBankParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    expected = {
        "occurred_at",
        "device",
        "from_account_info",
        "counterparty",
        "merchant_code_1",
        "reference_2",
        "amount",
        "fee",
        "memo",
    }
    assert expected.issubset(transaction.raw_fields.keys())
    assert (
        transaction.raw_fields["from_account_info"]
        == "XXX-X-15441-X : นาย พิชเยนทร์ เย็นศิริ ออมทรัพย์"
    )
