"""Tests for app.parsers.kbank.mapper."""

from app.parsers.kbank.mapper import map_fields


def test_maps_english_fields_to_canonical():
    raw_fields = [
        ("Transaction Date", "26/01/2025"),
        ("Amount", "1,500.00 THB"),
        ("Fee", "0.00 THB"),
        ("Available Balance", "25,430.50 THB"),
        ("Reference No", "202501261432001234"),
        ("Status", "Success"),
    ]
    canonical = map_fields(raw_fields)

    assert canonical.transaction_date == "2025-01-26"
    assert canonical.amount == 1500.00
    assert canonical.fee == 0.00
    assert canonical.balance == 25430.50
    assert canonical.reference == "202501261432001234"
    assert canonical.status == "Success"
    assert canonical.warnings == []


def test_maps_thai_date_with_buddhist_era():
    raw_fields = [("วันที่ทำรายการ", "26 ม.ค. 2568")]
    canonical = map_fields(raw_fields)
    assert canonical.transaction_date == "2025-01-26"


def test_maps_thai_amount_fields():
    raw_fields = [
        ("จำนวนเงิน", "1,500.00 บาท"),
        ("ค่าธรรมเนียม", "0.00 บาท"),
        ("ยอดเงินคงเหลือ", "25,430.50 บาท"),
    ]
    canonical = map_fields(raw_fields)
    assert canonical.amount == 1500.00
    assert canonical.fee == 0.00
    assert canonical.balance == 25430.50


def test_unrecognized_label_is_kept_in_raw_fields_only():
    raw_fields = [("Some Unknown Label", "some value")]
    canonical = map_fields(raw_fields)
    assert canonical.raw_fields == {"Some Unknown Label": "some value"}
    assert canonical.amount is None


def test_first_value_wins_for_duplicate_canonical_fields():
    raw_fields = [
        ("Amount", "100.00 THB"),
        ("จำนวนเงิน", "999.00 บาท"),
    ]
    canonical = map_fields(raw_fields)
    assert canonical.amount == 100.00
