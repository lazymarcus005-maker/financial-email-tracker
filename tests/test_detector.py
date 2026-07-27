"""Tests for app.parsers.kbank.detector."""

from app.parsers.kbank.detector import detect_section

ENGLISH_EMAIL = """
Transfer Successful

Transaction Date : 26/01/2025
Amount : 1,500.00 THB
Fee : 0.00 THB
Reference No : 202501261432001234
Status : Success

Thank you for using K PLUS.
"""

THAI_EMAIL = """
รายการโอนเงินสำเร็จ

วันที่ทำรายการ : 2025-01-26
จำนวนเงิน : 1,500.00 บาท
ค่าธรรมเนียม : 0.00 บาท
หมายเลขอ้างอิง : 202501261432001234
สถานะ : สำเร็จ

ขอบคุณที่ใช้บริการ K PLUS
"""

BILINGUAL_EMAIL = THAI_EMAIL + "\n" + ENGLISH_EMAIL


def test_detects_english_section():
    result = detect_section(ENGLISH_EMAIL)
    assert result.language == "en"
    assert "Transaction Date" in result.section_text


def test_detects_thai_section():
    result = detect_section(THAI_EMAIL)
    assert result.language == "th"
    assert "วันที่ทำรายการ" in result.section_text


def test_picks_a_section_in_bilingual_email():
    # Both language blocks have the same number of fields; English wins ties.
    result = detect_section(BILINGUAL_EMAIL)
    assert result.language == "en"
    assert "Transaction Date" in result.section_text
    assert "วันที่ทำรายการ" not in result.section_text


def test_falls_back_to_whole_text_when_no_label_lines():
    text = "Just a plain message with no fields."
    result = detect_section(text)
    assert result.section_text == text
