"""LINE daily summary: real DB aggregation -> formatted message -> mocked LINE push,
covering the message format, HTTP failure handling, and that the channel token
never ends up in a log line.
"""

import asyncio
import json
from datetime import date

import httpx

from app.config import Settings
from app.ingestion import scheduler
from app.logging_config import configure_logging
from app.storage import database

SECRET_TOKEN = "secret-line-token-xyz-should-never-be-logged"
TODAY = date.today().isoformat()


def _settings(**overrides):
    defaults = dict(
        SCHEDULE=["22:00"],
        TIMEZONE="Asia/Bangkok",
        LINE_USER_ID="U1234567890",
        LINE_CHANNEL_ACCESS_TOKEN=SECRET_TOKEN,
    )
    defaults.update(overrides)
    return Settings(**defaults)


async def _seed_transaction(db, **overrides):
    fields = dict(
        transaction_type="bank_transfer",
        direction="out",
        status="success",
        occurred_at=f"{TODAY}T10:00:00",
        amount=300.0,
        fee=0.0,
        available_balance=None,
        counterparty="Shopee",
        description="Payment",
        category="Shopping",
        category_source="rule",
        parser_version="1.0",
        parse_status="complete",
        parse_confidence=1.0,
        warnings_json="[]",
        raw_fields_json="{}",
        gmail_message_id="line-msg-1",
    )
    fields.update(overrides)
    await db.execute(
        """
        INSERT INTO transactions (
            transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (:transaction_type, :direction, :status, :occurred_at, :amount, :fee,
            :available_balance, :counterparty, :description, :category, :category_source,
            :parser_version, :parse_status, :parse_confidence, :warnings_json,
            :raw_fields_json, :gmail_message_id)
        """,
        fields,
    )
    await db.commit()


class FakeResponse:
    def __init__(self, raise_exc=None):
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc


class FakeAsyncClient:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, headers=None, json=None):
        self._calls.append({"url": url, "headers": headers, "json": json})
        return self._response


def _patch_line_http(monkeypatch, response):
    calls = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient(response, calls))
    return calls


async def _seed_two_transactions():
    db = await database.get_connection()
    await _seed_transaction(db, gmail_message_id="line-msg-in", direction="in", amount=5000.0, category=None)
    await _seed_transaction(db, gmail_message_id="line-msg-out", direction="out", amount=300.0, category="Shopping")
    await db.close()


def test_daily_summary_job_pushes_message_reflecting_real_db_data(temp_db_path, monkeypatch):
    asyncio.run(_seed_two_transactions())

    calls = _patch_line_http(monkeypatch, FakeResponse())

    scheduler.send_daily_summary_job(_settings())

    assert len(calls) == 1
    assert calls[0]["headers"]["Authorization"] == f"Bearer {SECRET_TOKEN}"
    assert calls[0]["json"]["to"] == "U1234567890"
    text = calls[0]["json"]["messages"][0]["text"]
    assert TODAY in text
    assert "5,000.00" in text
    assert "Shopping: ฿300.00" in text


def test_daily_summary_job_survives_line_api_failure(temp_db_path, monkeypatch, caplog):
    _patch_line_http(monkeypatch, FakeResponse(raise_exc=httpx.HTTPError("boom")))

    with caplog.at_level("INFO"):
        scheduler.send_daily_summary_job(_settings())  # must not raise

    events = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert any(e.get("event") == "ingestion_error" and e.get("job") == "daily_summary" for e in events)


def test_line_channel_token_never_appears_in_logs(temp_db_path, monkeypatch, tmp_path):
    configure_logging(level="INFO", fmt="json", log_dir=tmp_path / "logs")
    _patch_line_http(monkeypatch, FakeResponse())

    scheduler.send_daily_summary_job(_settings())
    # Also exercise the failure path - error messages are a common place to accidentally leak request state.
    _patch_line_http(monkeypatch, FakeResponse(raise_exc=httpx.HTTPError("boom")))
    scheduler.send_daily_summary_job(_settings())

    log_content = (tmp_path / "logs" / "app.log").read_text()
    assert SECRET_TOKEN not in log_content
