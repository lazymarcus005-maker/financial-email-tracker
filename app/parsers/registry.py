"""Parser registry - Select parser by bank sender."""

import logging
from app.parsers.base import BaseParser
from app.parsers.kbank.parser import KBankParser

logger = logging.getLogger(__name__)


class ParserRegistry:
    """Route emails to appropriate bank parser."""
    
    def __init__(self):
        self.parsers: dict[str, BaseParser] = {
            "kasikornbank": KBankParser(),
            # Future: "scb": SCBParser(), "ktb": KTBParser(), etc.
        }
    
    def get_parser(self, sender: str) -> BaseParser | None:
        """Select parser based on sender email."""
        sender_lower = sender.lower()
        
        for bank_key, parser in self.parsers.items():
            if bank_key in sender_lower or parser.can_handle(sender):
                logger.info(f"Parser selected: {parser.__class__.__name__} for {sender}")
                return parser
        
        logger.warning(f"No parser found for sender: {sender}")
        return None
    
    def parse(self, email_text: str, sender: str) -> dict | None:
        """Parse email, return Transaction dict or None if failed."""
        parser = self.get_parser(sender)
        if not parser:
            return None
        
        return parser.parse(email_text)
