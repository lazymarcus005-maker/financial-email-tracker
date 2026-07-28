"""Tests for app.ingestion.scheduler - cron job wiring and job entry points."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.ingestion import scheduler


def _settings(**overrides):
    defaults = dict(SCHEDULE=["05:00", "10:00", "14:00", "22:00"], TIMEZONE="Asia/Bangkok")
    defaults.update(overrides)
    return Settings(**defaults)


def test_build_scheduler_registers_one_job_per_schedule_slot():
    # build_scheduler() only registers jobs, it never starts the scheduler, so
    # there's no background thread to shut down afterwards.
    settings = _settings()
    sched = scheduler.build_scheduler(settings)
    jobs = {job.id: job for job in sched.get_jobs()}
    assert set(jobs) == {"cron-05:00", "cron-10:00", "cron-14:00", "cron-22:00"}


def test_build_scheduler_uses_cron_trigger_with_configured_timezone():
    settings = _settings()
    sched = scheduler.build_scheduler(settings)
    job = sched.get_job("cron-05:00")
    trigger_str = str(job.trigger)
    assert "hour='5'" in trigger_str
    assert "minute='0'" in trigger_str
    assert str(job.trigger.timezone) == "Asia/Bangkok"


def test_last_schedule_slot_runs_evening_job_others_run_ingestion_only():
    settings = _settings()
    sched = scheduler.build_scheduler(settings)
    assert sched.get_job("cron-22:00").func is scheduler.evening_job
    assert sched.get_job("cron-05:00").func is scheduler.run_ingestion_job
    assert sched.get_job("cron-10:00").func is scheduler.run_ingestion_job
    assert sched.get_job("cron-14:00").func is scheduler.run_ingestion_job


def test_build_scheduler_with_empty_schedule_registers_no_jobs():
    settings = _settings(SCHEDULE=[])
    sched = scheduler.build_scheduler(settings)
    assert sched.get_jobs() == []


def test_run_ingestion_job_logs_start_and_finish(monkeypatch, caplog):
    async def fake_run_ingestion(query, engine=None):
        return {"emails_checked": 2, "inserted": 1, "duplicates": 1, "failed": 0}

    monkeypatch.setattr(scheduler, "run_ingestion", fake_run_ingestion)

    with caplog.at_level("INFO"):
        summary = scheduler.run_ingestion_job(_settings())

    assert summary == {"emails_checked": 2, "inserted": 1, "duplicates": 1, "failed": 0}
    events = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert {"event": "cron_start", "job": "ingestion"} in events
    assert any(e.get("event") == "cron_finish" and e.get("job") == "ingestion" for e in events)


def test_run_ingestion_job_logs_error_and_returns_none_on_exception(monkeypatch, caplog):
    async def fake_run_ingestion(query, engine=None):
        raise RuntimeError("gmail is down")

    monkeypatch.setattr(scheduler, "run_ingestion", fake_run_ingestion)

    with caplog.at_level("INFO"):
        result = scheduler.run_ingestion_job(_settings())

    assert result is None
    events = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert any(e.get("event") == "ingestion_error" and "gmail is down" in e.get("error", "") for e in events)


class _FakeDB:
    async def close(self):
        pass


def test_send_daily_summary_job_sends_via_line(monkeypatch, caplog):
    sent_calls = []

    async def fake_get_connection():
        return _FakeDB()

    async def fake_get_daily_summary_data(db):
        return {
            "date": "2026-07-27",
            "income_total": 100.0,
            "income_count": 1,
            "expense_by_category": {},
            "uncategorized_count": 0,
            "parse_error_count": 0,
            "last_sync": "2026-07-27T22:00:00",
        }

    async def fake_send_message(user_id, text, token):
        sent_calls.append((user_id, text, token))
        return True

    monkeypatch.setattr(scheduler, "get_connection", fake_get_connection)
    monkeypatch.setattr(scheduler, "get_daily_summary_data", fake_get_daily_summary_data)
    monkeypatch.setattr(scheduler, "send_message", fake_send_message)

    settings = _settings(LINE_USER_ID="user-1", LINE_CHANNEL_ACCESS_TOKEN="token-1")

    with caplog.at_level("INFO"):
        scheduler.send_daily_summary_job(settings)

    assert sent_calls == [("user-1", scheduler.format_daily_summary(
        {
            "date": "2026-07-27",
            "income_total": 100.0,
            "income_count": 1,
            "expense_by_category": {},
            "uncategorized_count": 0,
            "parse_error_count": 0,
            "last_sync": "2026-07-27T22:00:00",
        }
    ), "token-1")]
    events = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert any(e.get("event") == "daily_summary_sent" for e in events)


def test_send_daily_summary_job_logs_error_when_send_fails(monkeypatch, caplog):
    async def fake_get_connection():
        return _FakeDB()

    async def fake_get_daily_summary_data(db):
        return {
            "date": "2026-07-27",
            "income_total": 0.0,
            "income_count": 0,
            "expense_by_category": {},
            "uncategorized_count": 0,
            "parse_error_count": 0,
            "last_sync": None,
        }

    async def fake_send_message(user_id, text, token):
        return False

    monkeypatch.setattr(scheduler, "get_connection", fake_get_connection)
    monkeypatch.setattr(scheduler, "get_daily_summary_data", fake_get_daily_summary_data)
    monkeypatch.setattr(scheduler, "send_message", fake_send_message)

    with caplog.at_level("INFO"):
        scheduler.send_daily_summary_job(_settings())

    events = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert any(e.get("event") == "ingestion_error" and e.get("job") == "daily_summary" for e in events)


def test_evening_job_runs_ingestion_then_daily_summary(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "run_ingestion_job", lambda settings: calls.append("ingestion"))
    monkeypatch.setattr(scheduler, "send_daily_summary_job", lambda settings: calls.append("summary"))

    scheduler.evening_job(_settings())

    assert calls == ["ingestion", "summary"]


def test_next_scheduled_run_returns_next_slot_today():
    settings = _settings()
    now = datetime(2026, 7, 28, 6, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
    result = scheduler.next_scheduled_run(settings, now=now)
    assert result == datetime(2026, 7, 28, 10, 0, tzinfo=ZoneInfo("Asia/Bangkok"))


def test_next_scheduled_run_wraps_to_tomorrow():
    settings = _settings()
    now = datetime(2026, 7, 28, 23, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
    result = scheduler.next_scheduled_run(settings, now=now)
    assert result == datetime(2026, 7, 29, 5, 0, tzinfo=ZoneInfo("Asia/Bangkok"))


def test_next_scheduled_run_returns_none_for_empty_schedule():
    settings = _settings(SCHEDULE=[])
    assert scheduler.next_scheduled_run(settings) is None


def test_next_scheduled_run_skips_slot_at_exact_now():
    settings = _settings()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
    result = scheduler.next_scheduled_run(settings, now=now)
    assert result == datetime(2026, 7, 28, 14, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
