"""Tests for app.parsers.registry - bank routing and identification."""

from app.parsers.registry import ParserRegistry


def test_identify_bank_matches_known_senders():
    registry = ParserRegistry()
    assert registry.identify_bank("notify@kasikornbank.com") == "KBank"
    assert registry.identify_bank("admin@krungsri.com") == "Krungsri"
    assert registry.identify_bank("LHBYou@lhbank.co.th") == "LH Bank"
    assert registry.identify_bank("scbeasynet@scb.co.th") == "SCB"


def test_identify_bank_returns_none_for_unmatched_sender():
    registry = ParserRegistry()
    assert registry.identify_bank("someone@example.com") is None
