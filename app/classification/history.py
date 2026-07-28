"""History-based categorization - lookup/record counterparty -> category mappings."""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def lookup(
    db: aiosqlite.Connection, counterparty: str | None, owner_user_id: int | None = None
) -> str | None:
    """Return the cached category for a counterparty, if one exists."""
    if not counterparty:
        return None

    where = ["counterparty = ?"]
    params: list = [counterparty]
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(owner_user_id)
    cursor = await db.execute(f"SELECT category FROM counterparty_mapping WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return row["category"] if row else None


async def record(
    db: aiosqlite.Connection,
    counterparty: str | None,
    category: str,
    source: str = "manual",
    owner_user_id: int | None = None,
) -> None:
    """Upsert a counterparty -> category mapping, e.g. after a manual override."""
    if not counterparty:
        return

    if owner_user_id is None:
        cursor = await db.execute(
            """
            UPDATE counterparty_mapping
            SET category = ?, source = ?
            WHERE counterparty = ? AND owner_user_id IS NULL
            """,
            (category, source, counterparty),
        )
        if cursor.rowcount == 0:
            await db.execute(
                """
                INSERT INTO counterparty_mapping (owner_user_id, counterparty, category, source)
                VALUES (NULL, ?, ?, ?)
                """,
                (counterparty, category, source),
            )
        logger.info(f"Recorded mapping: {counterparty!r} -> {category!r} (source={source})")
        return

    await db.execute(
        """
        INSERT INTO counterparty_mapping (owner_user_id, counterparty, category, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(owner_user_id, counterparty) DO UPDATE SET
            category = excluded.category,
            source = excluded.source
        """,
        (owner_user_id, counterparty, category, source),
    )
    logger.info(f"Recorded mapping: {counterparty!r} -> {category!r} (source={source})")
