"""Tests for app.storage.queries - dashboard aggregation helpers."""

from datetime import date

import pytest

from app.storage import queries


@pytest.mark.asyncio
async def test_get_expense_by_bank_groups_and_sums(db_connection):
    today = date.today().isoformat()
    await db_connection.execute(
        """
        INSERT INTO transactions (
            transaction_type, direction, status, occurred_at, amount, category_source,
            parse_status, bank, gmail_message_id
        ) VALUES
            ('bank_transfer', 'out', 'success', ?, 100.0, 'rule', 'complete', 'KBank', 'm1'),
            ('bank_transfer', 'out', 'success', ?, 50.0, 'rule', 'complete', 'KBank', 'm2'),
            ('bank_transfer', 'out', 'success', ?, 75.0, 'rule', 'complete', 'SCB', 'm3'),
            ('bank_transfer', 'out', 'success', ?, 25.0, 'rule', 'complete', NULL, 'm4'),
            ('bank_transfer', 'in', 'success', ?, 1000.0, 'rule', 'complete', 'KBank', 'm5')
        """,
        (today, today, today, today, today),
    )
    await db_connection.commit()

    rows = await queries.get_expense_by_bank(db_connection, days=7)

    by_bank = {r["bank"]: r["total"] for r in rows}
    assert by_bank == {"KBank": 150.0, "SCB": 75.0, "Unknown": 25.0}


def test_build_pie_segments_percentages_sum_to_total():
    rows = [{"bank": "KBank", "total": 150.0}, {"bank": "SCB", "total": 50.0}]
    segments = queries.build_pie_segments(rows)

    assert [s["bank"] for s in segments] == ["KBank", "SCB"]
    assert segments[0]["pct"] == pytest.approx(75.0)
    assert segments[1]["pct"] == pytest.approx(25.0)
    assert segments[0]["dashoffset"] == "-0.0000"
    assert segments[0]["color"] == queries.BANK_COLORS["KBank"]


def test_build_pie_segments_handles_zero_total():
    assert queries.build_pie_segments([]) == []
    segments = queries.build_pie_segments([{"bank": "KBank", "total": 0.0}])
    assert segments[0]["pct"] == 0.0
