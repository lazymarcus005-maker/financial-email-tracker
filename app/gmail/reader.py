"""Gmail Reader - high level service to fetch and normalize bank emails."""

import html as html_module
import logging
import re

from app.gmail import EmailMessage
from app.gmail.client import GmailClient

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class GmailReader:
    """Reads bank notification emails from Gmail and returns clean EmailMessage objects."""

    def __init__(self, client: GmailClient):
        self.client = client

    def read(self, query: str, max_results: int = 100) -> list[EmailMessage]:
        """Fetch emails matching query, ensuring body_text is always populated."""
        messages = self.client.fetch_messages(query, max_results=max_results)

        for message in messages:
            if not message.body_text.strip() and message.body_html:
                logger.debug(f"Falling back to HTML->text for message {message.gmail_message_id}")
                message.body_text = html_to_text(message.body_html)

        logger.info(f"Read {len(messages)} emails for query: {query}")
        return messages


def html_to_text(html: str) -> str:
    """Small HTML->text fallback: strip tags, unescape entities, collapse whitespace."""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
