"""Cron scheduler - ingests new emails on a schedule and sends the daily LINE summary."""

import asyncio
import json
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.classification.engine import CategoryEngine
from app.config import Settings, get_settings
from app.ingestion.service import run_ingestion
from app.integrations.line import format_daily_summary, send_message
from app.storage.database import get_connection
from app.storage.queries import get_daily_summary_data

logger = logging.getLogger(__name__)


def _log_event(event: str, **fields) -> None:
    """Emit a structured JSON log line for a scheduler lifecycle event."""
    logger.info(json.dumps({"event": event, **fields}, default=str))


def _build_engine(settings: Settings) -> CategoryEngine:
    return CategoryEngine(
        ai_enabled=settings.AI_ENABLED,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_MODEL,
    )


def run_ingestion_job(settings: Settings) -> dict | None:
    """Run one ingestion pass. Synchronous entry point suitable for APScheduler."""
    _log_event("cron_start", job="ingestion")
    try:
        summary = asyncio.run(run_ingestion(settings.GMAIL_QUERY, engine=_build_engine(settings)))
        _log_event("cron_finish", job="ingestion", **summary)
        return summary
    except Exception as e:
        logger.exception("Ingestion cron job failed")
        _log_event("ingestion_error", job="ingestion", error=str(e))
        return None


async def _send_daily_summary_async(settings: Settings) -> bool:
    db = await get_connection()
    try:
        data = await get_daily_summary_data(db)
    finally:
        await db.close()

    text = format_daily_summary(data)
    return await send_message(settings.LINE_USER_ID, text, settings.LINE_CHANNEL_ACCESS_TOKEN)


def send_daily_summary_job(settings: Settings) -> None:
    """Aggregate today's transactions and push the LINE summary. Sync entry point for APScheduler."""
    _log_event("cron_start", job="daily_summary")
    try:
        sent = asyncio.run(_send_daily_summary_async(settings))
        if sent:
            _log_event("daily_summary_sent")
        else:
            _log_event("ingestion_error", job="daily_summary", error="LINE send_message returned False")
    except Exception as e:
        logger.exception("Daily summary job failed")
        _log_event("ingestion_error", job="daily_summary", error=str(e))


def evening_job(settings: Settings) -> None:
    """Last scheduled slot of the day: ingest, then send the daily summary."""
    run_ingestion_job(settings)
    send_daily_summary_job(settings)


def build_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    """Build (but do not start) the cron scheduler from `SCHEDULE`/`TIMEZONE` config.

    The last time in `SCHEDULE` also triggers the daily LINE summary after ingesting.
    """
    settings = settings or get_settings()
    scheduler = BackgroundScheduler(timezone=settings.TIMEZONE)

    summary_slot = settings.SCHEDULE[-1] if settings.SCHEDULE else None

    for time_str in settings.SCHEDULE:
        hour, minute = (int(part) for part in time_str.split(":"))
        job = evening_job if time_str == summary_slot else run_ingestion_job
        scheduler.add_job(
            job,
            CronTrigger(hour=hour, minute=minute, timezone=settings.TIMEZONE),
            args=[settings],
            id=f"cron-{time_str}",
            replace_existing=True,
        )
        logger.info(f"Scheduled {job.__name__} at {time_str} {settings.TIMEZONE}")

    return scheduler


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    scheduler = build_scheduler(settings)
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


if __name__ == "__main__":
    import time as time_module

    logging.basicConfig(level=logging.INFO)
    sched = start_scheduler()
    try:
        while True:
            time_module.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
