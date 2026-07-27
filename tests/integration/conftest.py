"""Shared fixtures for full-pipeline integration tests (Gmail -> parse -> persist -> categorize).

Reuses `temp_db_path`/`db_connection` from the top-level tests/conftest.py (pytest
cascades conftest.py fixtures into subdirectories automatically).
"""

from datetime import datetime

import pytest

from app.gmail import EmailMessage

KBANK_SENDER = "K PLUS <noreply@kasikornbank.com>"

ENGLISH_TRANSFER_EMAIL = """
Transfer Successful

Transaction Date : 26/01/2025
Transaction Time : 14:32
Amount : 1,500.00 THB
Fee : 0.00 THB
Available Balance : 25,430.50 THB
Reference No : 202501261432001234
Status : Success
"""

THAI_TRANSFER_EMAIL = """
รายการโอนเงินสำเร็จ

วันที่ทำรายการ : 26 ม.ค. 2568
เวลาทำรายการ : 14:32
จำนวนเงิน : 1,500.00 บาท
ค่าธรรมเนียม : 0.00 บาท
ยอดเงินคงเหลือ : 25,430.50 บาท
หมายเลขอ้างอิง : 202501261432001234
สถานะ : สำเร็จ
"""


class FakeReader:
    """Stands in for GmailReader: `.read(query, max_results=100)` returns a canned list."""

    def __init__(self, messages):
        self._messages = messages

    def read(self, query, max_results=100):
        return self._messages


@pytest.fixture
def make_message():
    """Factory fixture: build an EmailMessage with sensible KBank defaults."""

    def _make(message_id, body_text, subject="K PLUS: You have sent money successfully", sender=KBANK_SENDER):
        return EmailMessage(
            gmail_message_id=message_id,
            gmail_thread_id=f"thread-{message_id}",
            sender=sender,
            subject=subject,
            received_at=datetime(2025, 1, 26, 14, 32),
            body_text=body_text,
        )

    return _make


@pytest.fixture
def fake_reader():
    """Factory fixture: wrap a list of EmailMessage in a FakeReader."""

    def _build(messages):
        return FakeReader(messages)

    return _build


@pytest.fixture
def english_transfer_email():
    return ENGLISH_TRANSFER_EMAIL


@pytest.fixture
def thai_transfer_email():
    return THAI_TRANSFER_EMAIL
