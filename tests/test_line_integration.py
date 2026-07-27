"""Tests for app.integrations.line - send_message and format_daily_summary."""

import httpx
import pytest

from app.integrations import line


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


def _patch_client(monkeypatch, response):
    calls = []

    def factory(*args, **kwargs):
        return FakeAsyncClient(response, calls)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


@pytest.mark.asyncio
async def test_send_message_skips_when_token_missing():
    sent = await line.send_message("user-1", "hello", channel_access_token=None)
    assert sent is False


@pytest.mark.asyncio
async def test_send_message_skips_when_user_id_missing():
    sent = await line.send_message(None, "hello", channel_access_token="token")
    assert sent is False


@pytest.mark.asyncio
async def test_send_message_success(monkeypatch):
    calls = _patch_client(monkeypatch, FakeResponse())

    sent = await line.send_message("user-1", "hello", channel_access_token="secret-token")

    assert sent is True
    assert len(calls) == 1
    assert calls[0]["url"] == line.LINE_PUSH_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert calls[0]["json"] == {"to": "user-1", "messages": [{"type": "text", "text": "hello"}]}


@pytest.mark.asyncio
async def test_send_message_returns_false_on_http_error(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(raise_exc=httpx.HTTPError("boom")))

    sent = await line.send_message("user-1", "hello", channel_access_token="secret-token")

    assert sent is False


def test_format_daily_summary_includes_all_sections():
    data = {
        "date": "2026-07-27",
        "income_total": 5000.0,
        "income_count": 2,
        "expense_by_category": {"Shopping": 300.0, "Transfer": 100.0, "Food": 50.0},
        "uncategorized_count": 3,
        "parse_error_count": 1,
        "last_sync": "2026-07-27T22:00:00",
    }

    text = line.format_daily_summary(data)

    assert "2026-07-27" in text
    assert "5,000.00" in text
    assert "2 รายการ" in text
    assert "Shopping: ฿300.00" in text
    assert "Transfer: ฿100.00" in text
    # Food isn't a known bucket, so it should roll up into the "other" total.
    assert "อื่นๆ: ฿50.00" in text
    assert "ยังไม่ได้แบ่งหมวดหมู่: 3 รายการ" in text
    assert "Parse Error: 1 รายการ" in text
    assert "Last Sync: 2026-07-27T22:00:00" in text


def test_format_daily_summary_defaults_last_sync_to_na():
    data = {
        "date": "2026-07-27",
        "income_total": 0.0,
        "income_count": 0,
        "expense_by_category": {},
        "uncategorized_count": 0,
        "parse_error_count": 0,
        "last_sync": None,
    }

    text = line.format_daily_summary(data)

    assert "Last Sync: N/A" in text
