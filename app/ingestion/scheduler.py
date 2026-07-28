"""Cron scheduler - ingests new emails on a schedule and sends the daily LINE summary."""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.classification.engine import CategoryEngine
from app.config import Settings, get_settings
from app.gmail.authorize import token_exists, user_token_path
from app.ingestion.service import run_ingestion
from app.integrations.line import format_daily_summary, send_message
from app.logging_config import log_event
from app.storage.database import get_connection
from app.storage.queries import get_daily_summary_data, get_default_owner_user_id, list_users

logger = logging.getLogger(__name__)


def _log_event(event: str, **fields) -> None:
    """Emit a structured JSON log line for a scheduler lifecycle event."""
    log_event(logger, event, **fields)


def _build_engine(settings: Settings) -> CategoryEngine:
    return CategoryEngine(
        ai_enabled=settings.AI_ENABLED,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_MODEL,
    )


def next_scheduled_run(settings: Settings, now: datetime | None = None) -> datetime | None:
    """The next SCHEDULE slot strictly after `now`, wrapping to tomorrow if needed."""
    if not settings.SCHEDULE:
        return None

    tz = ZoneInfo(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    today = now.date()

    todays_slots = []
    for time_str in settings.SCHEDULE:
        hour, minute = (int(part) for part in time_str.split(":"))
        todays_slots.append(datetime(today.year, today.month, today.day, hour, minute, tzinfo=tz))

    upcoming_today = [slot for slot in todays_slots if slot > now]
    if upcoming_today:
        return min(upcoming_today)

    tomorrow = today + timedelta(days=1)
    hour, minute = (int(part) for part in settings.SCHEDULE[0].split(":"))
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=tz)


def _empty_summary() -> dict:
    return {"emails_checked": 0, "inserted": 0, "duplicates": 0, "failed": 0}


def _add_summary(total: dict, summary: dict) -> None:
    for key in ("emails_checked", "inserted", "duplicates", "failed"):
        total[key] = total.get(key, 0) + summary.get(key, 0)


async def _connected_active_user_ids() -> list[int]:
    db = await get_connection()
    try:
        users = await list_users(db)
    finally:
        await db.close()
    return [user["id"] for user in users if user.get("is_active") and token_exists(user_token_path(user["id"]))]


async def _run_ingestion_for_connected_users(settings: Settings) -> dict:
    owner_ids = await _connected_active_user_ids()
    if not owner_ids:
        _log_event("cron_no_connected_users", job="ingestion")
        return _empty_summary()

    total = _empty_summary()
    for owner_user_id in owner_ids:
        try:
            summary = await run_ingestion(
                settings.GMAIL_QUERY,
                engine=_build_engine(settings),
                owner_user_id=owner_user_id,
            )
            _add_summary(total, summary)
            _log_event("cron_user_finish", job="ingestion", owner_user_id=owner_user_id, **summary)
        except Exception as e:
            logger.exception("Ingestion cron job failed for user %s", owner_user_id)
            _log_event("ingestion_error", job="ingestion", owner_user_id=owner_user_id, error=str(e))
    return total


def run_ingestion_job(settings: Settings) -> dict | None:
    """Run one ingestion pass. Synchronous entry point suitable for APScheduler."""
    _log_event("cron_start", job="ingestion")
    try:
        summary = asyncio.run(_run_ingestion_for_connected_users(settings))
        _log_event("cron_finish", job="ingestion", **summary)
        return summary
    except Exception as e:
        logger.exception("Ingestion cron job failed")
        _log_event("ingestion_error", job="ingestion", error=str(e))
        return None


async def _send_daily_summary_async(settings: Settings) -> bool:
    db = await get_connection()
    try:
        owner_user_id = await get_default_owner_user_id(db) if hasattr(db, "execute") else None
        if owner_user_id is None:
            data = await get_daily_summary_data(db)
        else:
            data = await get_daily_summary_data(db, owner_user_id=owner_user_id)
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

    from app.logging_config import configure_logging

    _settings = get_settings()
    configure_logging(level=_settings.LOG_LEVEL, fmt=_settings.LOG_FORMAT)
    sched = start_scheduler(_settings)
    try:
        while True:
            time_module.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
