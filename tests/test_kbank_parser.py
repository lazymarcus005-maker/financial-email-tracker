"""Integration tests for the full KBank parser pipeline."""

from app.parsers.kbank.parser import KBankParser

ENGLISH_EMAIL = """
Transfer Successful

Transaction Date : 26/01/2025
Transaction Time : 14:32
Amount : 1,500.00 THB
Fee : 0.00 THB
Available Balance : 25,430.50 THB
Reference No : 202501261432001234
Status : Success
"""

THAI_EMAIL = """
รายการโอนเงินสำเร็จ

วันที่ทำรายการ : 26 ม.ค. 2568
เวลาทำรายการ : 14:32
จำนวนเงิน : 1,500.00 บาท
ค่าธรรมเนียม : 0.00 บาท
ยอดเงินคงเหลือ : 25,430.50 บาท
หมายเลขอ้างอิง : 202501261432001234
สถานะ : สำเร็จ
"""


def test_parses_english_transfer_email():
    parser = KBankParser()
    transaction = parser.parse(ENGLISH_EMAIL, subject="K PLUS: You have sent money successfully")

    assert transaction is not None
    assert transaction.transaction_type == "bank_transfer"
    assert transaction.direction == "out"
    assert transaction.status == "success"
    assert transaction.amount == 1500.00
    assert transaction.available_balance == 25430.50
    assert transaction.occurred_at == "2025-01-26T14:32"
    assert transaction.parse_status == "complete"


def test_parses_thai_transfer_email():
    parser = KBankParser()
    transaction = parser.parse(THAI_EMAIL, subject="K PLUS: You have sent money successfully")

    assert transaction is not None
    assert transaction.amount == 1500.00
    assert transaction.available_balance == 25430.50
    assert transaction.occurred_at == "2025-01-26T14:32"


def test_returns_none_when_no_fields_found():
    parser = KBankParser()
    transaction = parser.parse("This email has no recognizable fields at all.", subject="Notice")
    assert transaction is None


def test_can_handle_matches_kasikornbank_and_kplus_senders():
    parser = KBankParser()
    assert parser.can_handle("KPLUS@kasikornbank.com")
    assert parser.can_handle("noreply@kplus.com")
    assert not parser.can_handle("noreply@scb.co.th")
