"""Integration tests for the full Krungsri parser pipeline."""

import re
from pathlib import Path

from app.parsers.krungsri.parser import KrungsriParser

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "krungsri_samples.txt"


def _load_samples() -> dict[int, str]:
    """Split the fixture file into a {sample_number: email_text} mapping."""
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    parts = re.split(r"=== Krungsri Email Sample (\d+) ===\n", text)
    # parts[0] is the preamble (empty); then alternating number, body.
    samples = {}
    for i in range(1, len(parts), 2):
        samples[int(parts[i])] = parts[i + 1].strip("\n")
    return samples


SAMPLES = _load_samples()


def test_parses_sample_1_scb_shop():
    parser = KrungsriParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert transaction.amount == 95.00
    assert transaction.fee == 0.0
    assert transaction.counterparty == "SCB มณี SHOP (มักยำ แซ่บแสบดาก)"
    assert transaction.raw_fields["merchant_code"] == "014000008216491"
    assert transaction.occurred_at == "2026-07-03T19:21:13"
    assert transaction.transaction_type == "bill_payment"
    assert transaction.status == "success"
    assert transaction.direction == "out"
    assert transaction.parse_status == "complete"


def test_parses_sample_2_mea():
    parser = KrungsriParser()
    transaction = parser.parse(SAMPLES[2])

    assert transaction is not None
    assert transaction.amount == 939.65
    assert transaction.counterparty == "METROPOLITAN ELECTRICITY AUTHORITY"
    assert transaction.raw_fields["reference_1"] == "017374219000002280"
    assert transaction.raw_fields["reference_2"] == "025505276505060769"
    assert transaction.occurred_at == "2026-07-04T08:03:31"


def test_parses_sample_3_cafe_amazon():
    parser = KrungsriParser()
    transaction = parser.parse(SAMPLES[3])

    assert transaction is not None
    assert transaction.amount == 70.00
    assert transaction.counterparty == "คาเฟ่อเมซอน พหลโยธิน เพลส พลาซ่า"
    assert transaction.raw_fields["transaction_code"] == "APIC1782779405977NMT"
    assert transaction.occurred_at == "2026-06-30T07:30:10"


def test_can_handle_krungsri_sender():
    parser = KrungsriParser()
    assert parser.can_handle("admin@krungsri.com")
    assert parser.can_handle("noreply@krungsri.com")


def test_cannot_handle_kbank_sender():
    parser = KrungsriParser()
    assert not parser.can_handle("KPLUS@kasikornbank.com")


def test_memo_captured_even_when_empty():
    parser = KrungsriParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    assert "memo" in transaction.raw_fields
    assert transaction.raw_fields["memo"] == ""


def test_all_raw_fields_captured():
    parser = KrungsriParser()
    transaction = parser.parse(SAMPLES[1])

    assert transaction is not None
    expected = {
        "result",
        "transaction_type",
        "account_name",
        "counterparty",
        "amount",
        "fee",
        "merchant_code",
        "merchant_reference",
        "reference_number",
        "occurred_at",
        "memo",
    }
    assert expected.issubset(transaction.raw_fields.keys())
    assert transaction.raw_fields["account_name"] == "PICHAYEAN YEN"
    assert transaction.raw_fields["reference_number"] == "KSA00000000681681090"
