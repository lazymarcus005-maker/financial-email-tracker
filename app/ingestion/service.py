"""Ingestion Service - read Gmail, parse transactions, dedup, and persist to SQLite."""

import logging
import threading
import time

from app.classification.engine import CategoryEngine
from app.gmail.reader import GmailReader
from app.ingestion import persistence
from app.parsers.registry import ParserRegistry
from app.storage import queries
from app.storage.database import get_connection

logger = logging.getLogger(__name__)

_INGESTION_LOCK = threading.Lock()


class IngestionAlreadyRunningError(RuntimeError):
    """Raised when another ingestion run is already active in this process."""


async def run_ingestion(
    query: str,
    reader: GmailReader | None = None,
    registry: ParserRegistry | None = None,
    engine: CategoryEngine | None = None,
) -> dict:
    """Read emails matching `query`, parse them, and persist new transactions.

    Returns a summary dict with counts: emails_checked, inserted, duplicates, failed.
    """
    if not _INGESTION_LOCK.acquire(blocking=False):
        raise IngestionAlreadyRunningError("An ingestion run is already in progress")

    started_at = time.monotonic()
    reader = reader or GmailReader()
    registry = registry or ParserRegistry()
    engine = engine or CategoryEngine()

    try:
        inserted = duplicates = failed = 0
        db = await get_connection()

        try:
            effective_query = await queries.apply_ignored_subjects_to_gmail_query(db, query)
            messages = reader.read(effective_query)

            for message in messages:
                if await queries.is_subject_ignored(db, message.subject):
                    logger.info(
                        f"Skipping ignored subject for message {message.gmail_message_id} ({message.subject!r})"
                    )
                    continue

                if await persistence.already_ingested(db, message.gmail_message_id):
                    logger.info(f"Skipping duplicate message {message.gmail_message_id}")
                    duplicates += 1
                    continue

                transaction = registry.parse(message.body_text, message.sender, subject=message.subject)

                if transaction is not None and transaction.parse_status == "ignored":
                    logger.info(f"Skipping ignored message {message.gmail_message_id} ({message.subject!r})")
                    continue

                if transaction is None or transaction.parse_status == "failed":
                    await persistence.insert_unknown(db, message, transaction)
                    await db.commit()
                    failed += 1
                    continue

                if await persistence.find_duplicate_transaction(db, transaction):
                    logger.info(
                        f"Skipping duplicate transaction (reference/fingerprint match) for message {message.gmail_message_id}"
                    )
                    await persistence.clear_unknown(db, message.gmail_message_id)
                    await db.commit()
                    duplicates += 1
                    continue

                category, category_source = await engine.categorize(
                    db, persistence.transaction_to_dict(transaction)
                )
                await persistence.insert_transaction(db, message, transaction, category, category_source)
                await persistence.clear_unknown(db, message.gmail_message_id)
                await db.commit()
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
    finally:
        _INGESTION_LOCK.release()
