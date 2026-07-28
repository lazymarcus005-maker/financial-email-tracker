"""LH Bank Email Normalizer - Clean up text before parsing."""

import re
import logging

logger = logging.getLogger(__name__)


def normalize(text: str) -> str:
    """Normalize email text."""

    # Remove BOM
    text = text.replace("\ufeff", "")

    # Remove NBSP (non-breaking space)
    text = text.replace("\xa0", " ")

    # Remove zero-width characters
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    # Normalize newlines
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\r", "\n", text)

    # Remove Slack-style timestamps [HH:MM AM/PM]
    text = re.sub(r"\[\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\]", "", text)

    # Strip excessive whitespace
    text = re.sub(r" +", " ", text)

    return text.strip()
