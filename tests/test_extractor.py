"""Tests for app.parsers.kbank.extractor."""

from app.parsers.kbank.extractor import extract_fields, is_label_line


def test_extracts_simple_colon_fields():
    text = "Transaction Date : 26/01/2025\nAmount : 1,500.00 THB"
    fields = extract_fields(text)
    assert fields == [
        ("Transaction Date", "26/01/2025"),
        ("Amount", "1,500.00 THB"),
    ]


def test_extracts_fullwidth_colon_fields():
    text = "วันที่ทำรายการ：2025-01-26"
    fields = extract_fields(text)
    assert fields == [("วันที่ทำรายการ", "2025-01-26")]


def test_ignores_lines_without_a_colon():
    text = "Transfer Successful\nAmount: 100.00 THB\nThank you"
    fields = extract_fields(text)
    assert fields == [("Amount", "100.00 THB")]


def test_preserves_duplicate_labels_in_order():
    text = "Amount: 100.00 THB\nAmount: 200.00 THB"
    fields = extract_fields(text)
    assert fields == [("Amount", "100.00 THB"), ("Amount", "200.00 THB")]


def test_is_label_line():
    assert is_label_line("Status: Success")
    assert not is_label_line("Just some text")
