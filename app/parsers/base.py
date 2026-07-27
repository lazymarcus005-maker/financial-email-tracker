"""Base parser interface - All bank parsers implement this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Transaction:
    """Canonical transaction representation."""
    
    transaction_type: str  # bank_transfer, bill_payment, etc.
    direction: str  # in, out, internal, unknown
    status: str  # success, failed, pending, cancelled, unknown
    occurred_at: str  # ISO format datetime
    amount: float
    fee: float = 0.0
    available_balance: float | None = None
    counterparty: str | None = None
    description: str | None = None
    parse_status: str = "complete"  # complete, partial, failed, ignored
    parse_confidence: float = 1.0  # 0.0 - 1.0
    parse_warnings: list[str] = None
    raw_fields: dict = None
    transaction_id: str | None = None  # bank's own reference/transaction number, if found

    def __post_init__(self):
        if self.parse_warnings is None:
            self.parse_warnings = []
        if self.raw_fields is None:
            self.raw_fields = {}


class BaseParser(ABC):
    """Base class for all bank parsers."""
    
    @abstractmethod
    def can_handle(self, sender: str) -> bool:
        """Check if this parser can handle the email sender."""
        pass
    
    @abstractmethod
    def parse(self, email_text: str, subject: str = "") -> Transaction | None:
        """Parse email, return Transaction or None if failed."""
        pass
