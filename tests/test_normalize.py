"""Tests for app.parsers.kbank.normalizer."""

from app.parsers.kbank.normalizer import normalize


def test_removes_bom():
    assert normalize("﻿Hello") == "Hello"


def test_removes_nbsp():
    assert normalize("Hello\xa0World") == "Hello World"


def test_removes_zero_width_chars():
    assert normalize("Hel​lo‌Wo‍rld") == "HelloWorld"


def test_normalizes_crlf_and_cr_newlines():
    assert normalize("Line1\r\nLine2\rLine3") == "Line1\nLine2\nLine3"


def test_collapses_repeated_spaces():
    assert normalize("Too    many   spaces") == "Too many spaces"


def test_strips_leading_and_trailing_whitespace():
    assert normalize("   padded text   ") == "padded text"


def test_preserves_thai_text():
    text = "วันที่ทำรายการ: 26 ม.ค. 2568"
    assert normalize(text) == text
