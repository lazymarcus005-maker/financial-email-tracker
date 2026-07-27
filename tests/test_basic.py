"""Unit tests stub."""

import pytest
from app.parsers.kbank.normalizer import normalize
from app.classification.engine import CategoryEngine


def test_normalizer_removes_bom():
    """Test BOM removal."""
    text = "\ufeff Hello World"
    assert normalize(text) == "Hello World"


def test_normalizer_removes_nbsp():
    """Test NBSP removal."""
    text = "Hello\xa0World"
    assert normalize(text) == "Hello World"


def test_category_engine_manual_override():
    """Test manual category override."""
    engine = CategoryEngine()
    category, source = engine.categorize(
        {"counterparty": "Shopee"},
        manual_override="Manual Shopping"
    )
    assert category == "Manual Shopping"
    assert source.value == "manual"


def test_category_engine_rule_based():
    """Test rule-based categorization."""
    engine = CategoryEngine()
    category, source = engine.categorize({"counterparty": "Shopee Mall"})
    assert category == "Shopping"
    assert source.value == "rule"


if __name__ == "__main__":
    pytest.main([__file__])
