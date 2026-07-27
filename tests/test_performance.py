"""Performance regression guards - parse throughput, and that dedup/query hot paths
are index-backed (not doing an accidental full table scan as the DB grows).

These aren't precise benchmarks (CI hardware varies); the budgets are generous on
purpose and exist to catch an accidental O(n^2)/full-scan regression, not to chase
absolute numbers.
"""

import time

import pytest

from app.classification import history
from app.ingestion import persistence
from app.parsers.kbank.parser import KBankParser
from app.storage import database, queries

ENGLISH_EMAIL_TEMPLATE = """
Transfer Successful

Transaction Date : 26/01/2025
Transaction Time : 14:32
Amount : 1,500.00 THB
Fee : 0.00 THB
Available Balance : 25,430.50 THB
Reference No : {ref}
Status : Success
"""

SUBJECT = "K PLUS: You have sent money successfully"


async def _seed_transactions(db, n: int) -> None:
    rows = [
        (
            f"TXN-{i}", "bank_transfer", "out", "success", "2026-07-27T10:00:00", float(i), 0.0,
            None, f"Merchant-{i}", "desc", "Uncategorized", "uncategorized", "1.0", "complete", 1.0,
            "[]", "{}", f"gmail-{i}",
        )
        for i in range(n)
    ]
    await db.executemany(
        """
        INSERT INTO transactions (
            transaction_id, transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    await db.commit()


# ---- Parsing throughput -------------------------------------------------------


def test_parses_100_emails_within_one_second_per_email_budget():
    parser = KBankParser()
    started = time.perf_counter()

    for i in range(100):
        transaction = parser.parse(ENGLISH_EMAIL_TEMPLATE.format(ref=f"REF-{i}"), subject=SUBJECT)
        assert transaction is not None
        assert transaction.parse_status == "complete"

    elapsed = time.perf_counter() - started
    avg_per_email = elapsed / 100
    assert avg_per_email < 1.0, f"parsing averaged {avg_per_email:.4f}s/email, budget is 1.0s/email"


# ---- Dedup index usage ---------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_lookups_stay_fast_across_1000_transactions(temp_db_path):
    db = await database.get_connection()
    await _seed_transactions(db, 1000)

    started = time.perf_counter()
    exists = await persistence.already_ingested(db, "gmail-999")
    missing = await persistence.already_ingested(db, "gmail-does-not-exist")
    elapsed = time.perf_counter() - started
    await db.close()

    assert exists is True
    assert missing is False
    assert elapsed < 0.1, f"1000-row dedup lookup took {elapsed:.4f}s, expected index-backed O(log n)"


@pytest.mark.asyncio
async def test_gmail_message_id_lookup_uses_unique_index(temp_db_path):
    db = await database.get_connection()
    cursor = await db.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM transactions WHERE gmail_message_id = ?", ("gmail-1",)
    )
    plan = "\n".join(row[3] for row in await cursor.fetchall())
    await cursor.close()
    await db.close()
    assert "SEARCH" in plan and "INDEX" in plan


@pytest.mark.asyncio
async def test_transaction_id_lookup_uses_unique_index(temp_db_path):
    db = await database.get_connection()
    cursor = await db.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM transactions WHERE transaction_id = ?", ("TXN-1",)
    )
    plan = "\n".join(row[3] for row in await cursor.fetchall())
    await cursor.close()
    await db.close()
    assert "SEARCH" in plan and "INDEX" in plan


# ---- Query performance ----------------------------------------------------------


@pytest.mark.asyncio
async def test_date_range_query_uses_occurred_at_index(temp_db_path):
    db = await database.get_connection()
    cursor = await db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM transactions WHERE occurred_at >= ? ORDER BY occurred_at DESC",
        ("2026-01-01",),
    )
    plan = "\n".join(row[3] for row in await cursor.fetchall())
    await cursor.close()
    await db.close()
    assert "idx_transactions_occurred_at" in plan


@pytest.mark.asyncio
async def test_list_transactions_stays_fast_across_1000_rows(temp_db_path):
    db = await database.get_connection()
    await _seed_transactions(db, 1000)

    started = time.perf_counter()
    items, total = await queries.list_transactions(db, page=1, page_size=20)
    elapsed = time.perf_counter() - started
    await db.close()

    assert total == 1000
    assert len(items) == 20
    assert elapsed < 0.2, f"paginated list query took {elapsed:.4f}s across 1000 rows"


# ---- Category (history) lookup speed --------------------------------------------


@pytest.mark.asyncio
async def test_category_history_lookup_stays_fast_across_500_counterparties(temp_db_path):
    db = await database.get_connection()
    for i in range(500):
        await history.record(db, f"Merchant-{i}", f"Category-{i % 10}")

    started = time.perf_counter()
    category = await history.lookup(db, "Merchant-499")
    elapsed = time.perf_counter() - started
    await db.close()

    assert category == "Category-9"
    assert elapsed < 0.05, f"history lookup took {elapsed:.4f}s across 500 counterparties"
