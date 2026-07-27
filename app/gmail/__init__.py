"""Gmail API integration - Authentication, reading, searching."""

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """Canonical email representation."""
    
    gmail_message_id: str
    gmail_thread_id: str
    sender: str
    subject: str
    received_at: datetime
    body_text: str
    body_html: str | None = None
