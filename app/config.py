"""App settings - loaded from config.yaml with {{ env.VAR }} placeholders resolved from the environment."""

import functools
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")

_ENV_PLACEHOLDER_RE = re.compile(r"\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _resolve_env_placeholders(value):
    if isinstance(value, str):
        match = _ENV_PLACEHOLDER_RE.fullmatch(value.strip())
        if match:
            return os.environ.get(match.group(1))
        return value
    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(v) for v in value]
    return value


@dataclass
class Settings:
    """Resolved application settings."""

    GMAIL_QUERY: str = ""
    GMAIL_CREDENTIALS_PATH: str = "secrets/credentials.json"
    GMAIL_TOKEN_PATH: str = "secrets/token.json"

    DATABASE_PATH: str = "data/finance.db"

    SCHEDULE: list = field(default_factory=list)
    TIMEZONE: str = "Asia/Bangkok"

    LINE_CHANNEL_ACCESS_TOKEN: str | None = None
    LINE_USER_ID: str | None = None

    AI_ENABLED: bool = False
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:1.7b"

    PARSER_VERSION: str = "1.0"

    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000

    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"


def load_settings(config_path: Path | str = CONFIG_PATH) -> Settings:
    """Load settings from a YAML config file, resolving env placeholders."""
    config_path = Path(config_path)

    if not config_path.exists():
        logger.warning(f"Config file not found at {config_path}, using defaults")
        return Settings()

    raw = yaml.safe_load(config_path.read_text()) or {}
    resolved = _resolve_env_placeholders(raw)

    known_fields = {f for f in Settings.__dataclass_fields__}
    kwargs = {k: v for k, v in resolved.items() if k in known_fields}
    return Settings(**kwargs)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return load_settings()
