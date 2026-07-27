"""Ingestion Service - read Gmail, parse transactions, dedup, and persist to SQLite."""

import logging
import time

from app.classification.engine import CategoryEngine
from app.gmail.reader import GmailReader
from app.ingestion import persistence
from app.parsers.registry import ParserRegistry
from app.storage.database import get_connection

logger = logging.getLogger(__name__)


async def run_ingestion(
    query: str,
    reader: GmailReader | None = None,
    registry: ParserRegistry | None = None,
    engine: CategoryEngine | None = None,
) -> dict:
    """Read emails matching `query`, parse them, and persist new transactions.

    Returns a summary dict with counts: emails_checked, inserted, duplicates, failed.
    """
    started_at = time.monotonic()
    reader = reader or GmailReader()
    registry = registry or ParserRegistry()
    engine = engine or CategoryEngine()

    messages = reader.read(query)

    inserted = duplicates = failed = 0
    db = await get_connection()

    try:
        for message in messages:
            if await persistence.already_ingested(db, message.gmail_message_id):
                logger.info(f"Skipping duplicate message {message.gmail_message_id}")
                duplicates += 1
                continue

            transaction = registry.parse(message.body_text, message.sender, subject=message.subject)

            if transaction is None or transaction.parse_status == "failed":
                await persistence.insert_unknown(db, message, transaction)
                failed += 1
                continue

            category, category_source = await engine.categorize(
                db, persistence.transaction_to_dict(transaction)
            )
            await persistence.insert_transaction(db, message, transaction, category, category_source.value)
            inserted += 1

        duration = time.monotonic() - started_at
        await persistence.record_run(
            db,
            emails_checked=len(messages),
            inserted=inserted,
            duplicates=duplicates,
            failed=failed,
            duration_seconds=duration,
        )
        await db.commit()
    finally:
        await db.close()

    summary = {
        "emails_checked": len(messages),
        "inserted": inserted,
        "duplicates": duplicates,
        "failed": failed,
    }
    logger.info(f"Ingestion complete: {summary}")
    return summary
