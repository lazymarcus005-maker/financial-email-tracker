"""History-based categorization - lookup/record counterparty -> category mappings."""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def lookup(db: aiosqlite.Connection, counterparty: str | None) -> str | None:
    """Return the cached category for a counterparty, if one exists."""
    if not counterparty:
        return None

    cursor = await db.execute(
        "SELECT category FROM counterparty_mapping WHERE counterparty = ?", (counterparty,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row["category"] if row else None


async def record(db: aiosqlite.Connection, counterparty: str | None, category: str, source: str = "manual") -> None:
    """Upsert a counterparty -> category mapping, e.g. after a manual override."""
    if not counterparty:
        return

    await db.execute(
        """
        INSERT INTO counterparty_mapping (counterparty, category, source)
        VALUES (?, ?, ?)
        ON CONFLICT(counterparty) DO UPDATE SET category = excluded.category, source = excluded.source
        """,
        (counterparty, category, source),
    )
    logger.info(f"Recorded mapping: {counterparty!r} -> {category!r} (source={source})")
