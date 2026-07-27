"""Structured (JSON) logging setup: console + rotating file handler, secrets masked.

Call `configure_logging()` once at process start (web app lifespan, scheduler
`__main__`, etc). `log_event()` is the shared helper for emitting the
lifecycle events used across ingestion/scheduler: email_parsed, duplicate,
categorized, cron_start, cron_finish, error.
"""

import json
import logging
import logging.handlers
import re
from pathlib import Path

from pythonjsonlogger import jsonlogger

DEFAULT_LOG_DIR = Path("logs")
MAX_BYTES = 5 * 1024 * 1024  # 5MB per file
BACKUP_COUNT = 10

_SECRET_KEY_RE = re.compile(r"token|credential|password|secret|authorization", re.IGNORECASE)
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_TEXT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class SecretMaskingFilter(logging.Filter):
    """Redact bearer tokens in the message, and any secret-looking `extra=` field."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _BEARER_RE.sub("Bearer ***REDACTED***", record.getMessage())
        record.args = ()
        for key, value in vars(record).items():
            if isinstance(value, str) and _SECRET_KEY_RE.search(key):
                setattr(record, key, "***REDACTED***")
        return True


class _JsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record)
        log_record["level"] = record.levelname
        log_record.setdefault("logger", record.name)


def configure_logging(level: str = "INFO", fmt: str = "json", log_dir: Path | str = DEFAULT_LOG_DIR) -> None:
    """Configure the root logger with a console handler and a rotating file handler.

    Idempotent: replaces any previously-installed handlers rather than
    stacking duplicates, so it's safe to call from both the web app and the
    standalone scheduler entrypoint in the same process.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = _JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT)
    secret_filter = SecretMaskingFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(secret_filter)
    root.addHandler(console_handler)

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(secret_filter)
    root.addHandler(file_handler)


def log_event(logger: logging.Logger, event: str, level: str = "info", **fields) -> None:
    """Emit a structured event as a JSON-encoded log message: {"event": ..., **fields}."""
    payload = json.dumps({"event": event, **fields}, default=str, ensure_ascii=False)
    getattr(logger, level)(payload)
