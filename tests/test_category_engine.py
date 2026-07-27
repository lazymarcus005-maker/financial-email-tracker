"""Tests for app.classification.engine - Manual > History > Rule > AI > Uncategorized."""

import pytest

from app.classification import history
from app.classification.engine import CategoryEngine


@pytest.mark.asyncio
async def test_manual_override_wins_over_everything(db_connection):
    await history.record(db_connection, "Shopee", "History Category")
    engine = CategoryEngine(ai_enabled=True)

    category, source = await engine.categorize(
        db_connection, {"counterparty": "Shopee"}, manual_override="Manual Category"
    )

    assert category == "Manual Category"
    assert source.value == "manual"


@pytest.mark.asyncio
async def test_history_wins_over_rule(db_connection):
    # "shopee" would normally match the rule-based "Shopping" category.
    await history.record(db_connection, "Shopee", "Learned Category")
    engine = CategoryEngine()

    category, source = await engine.categorize(db_connection, {"counterparty": "Shopee"})

    assert category == "Learned Category"
    assert source.value == "history"


@pytest.mark.asyncio
async def test_rule_based_when_no_history(db_connection):
    engine = CategoryEngine()

    category, source = await engine.categorize(db_connection, {"counterparty": "Shopee Mall"})

    assert category == "Shopping"
    assert source.value == "rule"


@pytest.mark.asyncio
async def test_uncategorized_when_nothing_matches(db_connection):
    engine = CategoryEngine()

    category, source = await engine.categorize(db_connection, {"counterparty": "Some Random Merchant"})

    assert category == "Uncategorized"
    assert source.value == "uncategorized"


@pytest.mark.asyncio
async def test_ai_used_when_enabled_and_no_history_or_rule_match(db_connection, monkeypatch):
    from app.classification import ai as ai_module

    async def fake_categorize(transaction, base_url, model, timeout=10.0):
        return "Food"

    monkeypatch.setattr(ai_module, "categorize", fake_categorize)
    engine = CategoryEngine(ai_enabled=True)

    category, source = await engine.categorize(db_connection, {"counterparty": "Some Random Merchant"})

    assert category == "Food"
    assert source.value == "ai"


@pytest.mark.asyncio
async def test_falls_back_to_uncategorized_when_ai_fails(db_connection, monkeypatch):
    from app.classification import ai as ai_module

    async def fake_categorize(transaction, base_url, model, timeout=10.0):
        return None

    monkeypatch.setattr(ai_module, "categorize", fake_categorize)
    engine = CategoryEngine(ai_enabled=True)

    category, source = await engine.categorize(db_connection, {"counterparty": "Some Random Merchant"})

    assert category == "Uncategorized"
    assert source.value == "uncategorized"


@pytest.mark.asyncio
async def test_ai_not_called_when_disabled(db_connection, monkeypatch):
    from app.classification import ai as ai_module

    async def boom(*args, **kwargs):
        raise AssertionError("AI should not be called when ai_enabled=False")

    monkeypatch.setattr(ai_module, "categorize", boom)
    engine = CategoryEngine(ai_enabled=False)

    category, source = await engine.categorize(db_connection, {"counterparty": "Some Random Merchant"})

    assert category == "Uncategorized"
    assert source.value == "uncategorized"


@pytest.mark.asyncio
async def test_no_counterparty_skips_history_and_rule(db_connection):
    engine = CategoryEngine()

    category, source = await engine.categorize(db_connection, {"counterparty": None})

    assert category == "Uncategorized"
    assert source.value == "uncategorized"


# ---- app.classification.history -------------------------------------------------


@pytest.mark.asyncio
async def test_history_lookup_returns_none_when_unseen(db_connection):
    assert await history.lookup(db_connection, "Never Seen Merchant") is None


@pytest.mark.asyncio
async def test_history_record_then_lookup_roundtrip(db_connection):
    await history.record(db_connection, "Netflix", "Entertainment")
    assert await history.lookup(db_connection, "Netflix") == "Entertainment"


@pytest.mark.asyncio
async def test_history_record_upserts_existing_counterparty(db_connection):
    await history.record(db_connection, "Netflix", "Subscription")
    await history.record(db_connection, "Netflix", "Entertainment", source="manual")

    assert await history.lookup(db_connection, "Netflix") == "Entertainment"

    cursor = await db_connection.execute("SELECT COUNT(*) AS n FROM counterparty_mapping")
    row = await cursor.fetchone()
    await cursor.close()
    assert row["n"] == 1
