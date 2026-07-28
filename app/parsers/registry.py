"""Parser registry - Select parser by bank sender."""

import logging
from app.parsers.base import BaseParser, Transaction
from app.parsers.kbank.parser import KBankParser
from app.parsers.krungsri.parser import KrungsriParser
from app.parsers.lhbank.parser import LHBankParser
from app.parsers.scb.parser import SCBParser

logger = logging.getLogger(__name__)


class ParserRegistry:
    """Route emails to the appropriate bank parser."""

    BANK_LABELS = {
        "kasikornbank": "KBank",
        "krungsri": "Krungsri",
        "lhbank": "LH Bank",
        "scb": "SCB",
    }

    def __init__(self):
        self._default_parser = KBankParser()
        self.parsers: dict[str, BaseParser] = {
            "kasikornbank": self._default_parser,
            "krungsri": KrungsriParser(),
            "lhbank": LHBankParser(),
            "scb": SCBParser(),
        }

    def get_parser(self, sender: str) -> BaseParser:
        """Select parser based on sender email. Falls back to KBank if no match."""
        sender_lower = sender.lower()

        for bank_key, parser in self.parsers.items():
            if bank_key in sender_lower or parser.can_handle(sender):
                logger.info(f"Parser selected: {parser.__class__.__name__} for {sender}")
                return parser

        logger.warning(f"No parser matched sender {sender!r}, falling back to KBank parser")
        return self._default_parser

    def identify_bank(self, sender: str) -> str | None:
        """Return a display label for the bank matching `sender`, or None.

        Uses the same matching rule as get_parser, but returns None on no
        match rather than falling back to KBank - being routed through the
        KBank parser as a fallback doesn't mean the email is actually from KBank.
        """
        sender_lower = sender.lower()
        for bank_key, parser in self.parsers.items():
            if bank_key in sender_lower or parser.can_handle(sender):
                return self.BANK_LABELS.get(bank_key)
        return None

    def parse(self, email_text: str, sender: str, subject: str = "") -> Transaction | None:
        """Parse email, return Transaction or None if failed."""
        parser = self.get_parser(sender)
        return parser.parse(email_text, subject=subject)
