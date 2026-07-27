"""Tests for app.logging_config - JSON formatting, secret masking, and log_event."""

import json
import logging

import pytest

from app.logging_config import SecretMaskingFilter, configure_logging, log_event


@pytest.fixture
def temp_log_dir(tmp_path):
    return tmp_path / "logs"


def test_configure_logging_creates_log_dir_and_file(temp_log_dir):
    configure_logging(level="INFO", fmt="json", log_dir=temp_log_dir)
    logging.getLogger("test.setup").info("hello")

    log_file = temp_log_dir / "app.log"
    assert log_file.exists()
    line = log_file.read_text().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"


def test_configure_logging_is_idempotent_no_duplicate_handlers(temp_log_dir):
    configure_logging(level="INFO", fmt="json", log_dir=temp_log_dir)
    configure_logging(level="INFO", fmt="json", log_dir=temp_log_dir)

    root = logging.getLogger()
    # One console + one file handler, not two of each.
    assert len(root.handlers) == 2


def test_configure_logging_text_format_uses_plain_formatter(temp_log_dir):
    configure_logging(level="INFO", fmt="text", log_dir=temp_log_dir)
    logging.getLogger("test.text").info("plain message")

    line = (temp_log_dir / "app.log").read_text().strip().splitlines()[-1]
    assert "plain message" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_secret_masking_filter_redacts_bearer_token():
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="Authorization: Bearer abc123.def-456_ghi", args=(), exc_info=None,
    )
    SecretMaskingFilter().filter(record)
    assert "abc123.def-456_ghi" not in record.msg
    assert "Bearer ***REDACTED***" in record.msg


def test_secret_masking_filter_redacts_secret_looking_extra_fields():
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="sent", args=(), exc_info=None,
    )
    record.channel_access_token = "super-secret-value"
    SecretMaskingFilter().filter(record)
    assert record.channel_access_token == "***REDACTED***"


def test_log_event_emits_json_with_event_and_fields(caplog):
    logger = logging.getLogger("test.event")
    with caplog.at_level("INFO"):
        log_event(logger, "email_parsed", gmail_message_id="msg-1", status="complete")

    payload = json.loads(caplog.records[-1].message)
    assert payload == {"event": "email_parsed", "gmail_message_id": "msg-1", "status": "complete"}
