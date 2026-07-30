"""Gmail API client - authenticate, search, and fetch full email messages."""

import base64
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.gmail import EmailMessage
from app.gmail.authorize import get_credentials

logger = logging.getLogger(__name__)


class GmailClient:
    """Thin wrapper around the Gmail API for searching and fetching messages."""

    def __init__(self, token_path=None):
        if token_path is None:
            raise ValueError("GmailClient requires a per-user token_path")
        self.token_path = str(token_path)
        self._profile_email: str | None = None
        creds = get_credentials(token_path=token_path)
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def get_profile_email(self) -> str | None:
        """Return the Gmail address behind the current OAuth token."""
        if self._profile_email is not None:
            return self._profile_email
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            self._profile_email = profile.get("emailAddress")
        except Exception as e:
            logger.warning("Could not read Gmail profile for token_path=%s: %s", self.token_path, e)
            self._profile_email = None
        return self._profile_email

    def search_message_ids(self, query: str, max_results: int = 100) -> list[str]:
        """Search Gmail for message ids matching a query, paginating as needed."""
        message_ids: list[str] = []
        page_token = None
        profile_email = self.get_profile_email()

        while True:
            try:
                response = (
                    self.service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        pageToken=page_token,
                        maxResults=min(500, max_results - len(message_ids)),
                    )
                    .execute()
                )
            except HttpError as e:
                logger.error(f"Gmail search failed for query={query!r}: {e}")
                raise

            message_ids.extend(m["id"] for m in response.get("messages", []))
            page_token = response.get("nextPageToken")

            if not page_token or len(message_ids) >= max_results:
                break

        logger.info(
            "Gmail search %r matched %s messages for profile=%s token_path=%s",
            query,
            len(message_ids),
            profile_email or "unknown",
            self.token_path or "default",
        )
        return message_ids[:max_results]

    def get_message(self, message_id: str) -> EmailMessage:
        """Fetch a single message and convert it into an EmailMessage."""
        raw = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return _to_email_message(raw)

    def fetch_messages(self, query: str, max_results: int = 100) -> list[EmailMessage]:
        """Search and fetch full EmailMessage objects for a query."""
        message_ids = self.search_message_ids(query, max_results=max_results)
        messages = []
        for message_id in message_ids:
            try:
                messages.append(self.get_message(message_id))
            except HttpError as e:
                logger.error(f"Failed to fetch message {message_id}: {e}")
        return messages


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part_data(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _walk_parts(payload: dict) -> list[dict]:
    """Flatten a MIME payload tree into a list of leaf parts."""
    parts = []
    if payload.get("parts"):
        for part in payload["parts"]:
            parts.extend(_walk_parts(part))
    else:
        parts.append(payload)
    return parts


def _extract_bodies(payload: dict) -> tuple[str, str | None]:
    """Extract (text/plain, text/html) bodies from a message payload."""
    body_text = ""
    body_html = None

    for part in _walk_parts(payload):
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if not data:
            continue

        try:
            raw_bytes = _decode_part_data(data)
            decoded = raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to decode message part ({mime_type}): {e}")
            continue

        if mime_type == "text/plain" and not body_text:
            body_text = decoded
        elif mime_type == "text/html" and body_html is None:
            body_html = decoded

    return body_text, body_html


def _to_email_message(raw: dict) -> EmailMessage:
    headers = raw.get("payload", {}).get("headers", [])
    sender = _header(headers, "From")
    subject = _header(headers, "Subject")
    date_header = _header(headers, "Date")

    received_at = None
    if date_header:
        try:
            received_at = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            received_at = None

    if received_at is None:
        received_at = datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000)

    body_text, body_html = _extract_bodies(raw.get("payload", {}))

    return EmailMessage(
        gmail_message_id=raw["id"],
        gmail_thread_id=raw.get("threadId", ""),
        sender=sender,
        subject=subject,
        received_at=received_at,
        body_text=body_text,
        body_html=body_html,
    )
