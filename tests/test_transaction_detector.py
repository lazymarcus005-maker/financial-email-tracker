"""Tests for app.parsers.kbank.transaction_detector."""

from app.parsers.kbank.mapper import CanonicalFields
from app.parsers.kbank.transaction_detector import detect


def test_detects_bank_transfer_success_from_english_subject():
    canonical = CanonicalFields(status="Success")
    attrs = detect("K PLUS: Transfer Successful", canonical)
    assert attrs.transaction_type == "bank_transfer"
    assert attrs.status == "success"


def test_detects_promptpay_transfer():
    canonical = CanonicalFields(status="สำเร็จ")
    attrs = detect("K PLUS: PromptPay Transfer Successful", canonical)
    assert attrs.transaction_type == "promptpay_transfer"
    assert attrs.status == "success"


def test_detects_out_direction_from_kbank_result_transfer_subject():
    canonical = CanonicalFields(status="Success")
    attrs = detect("Result of Funds Transfer (Success)", canonical)

    assert attrs.transaction_type == "bank_transfer"
    assert attrs.direction == "out"


def test_detects_bill_payment_direction_out():
    canonical = CanonicalFields()
    attrs = detect("K PLUS: Bill Payment Successful", canonical)
    assert attrs.transaction_type == "bill_payment"
    assert attrs.direction == "out"


def test_detects_deposit_direction_in():
    canonical = CanonicalFields()
    attrs = detect("K PLUS: Deposit Successful", canonical)
    assert attrs.transaction_type == "deposit"
    assert attrs.direction == "in"


def test_detects_failed_status():
    canonical = CanonicalFields()
    attrs = detect("K PLUS: Transfer Failed", canonical)
    assert attrs.status == "failed"


def test_falls_back_to_body_text_for_type():
    canonical = CanonicalFields()
    attrs = detect("K PLUS Notification", canonical, body_text="Your PromptPay transfer was completed")
    assert attrs.transaction_type == "promptpay_transfer"


def test_unknown_when_no_keywords_match():
    canonical = CanonicalFields()
    attrs = detect("K PLUS Notification", canonical)
    assert attrs.transaction_type == "unknown"
    assert attrs.status == "unknown"
    assert attrs.direction == "unknown"
