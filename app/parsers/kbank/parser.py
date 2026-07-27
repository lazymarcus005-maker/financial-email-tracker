"""KBank parser stub - Placeholder for full implementation."""

import logging
from app.parsers.base import BaseParser, Transaction

logger = logging.getLogger(__name__)


class KBankParser(BaseParser):
    """KBank (Kasikorn Bank) email parser."""
    
    def can_handle(self, sender: str) -> bool:
        """Check if this is a KBank email."""
        return "kasikornbank" in sender.lower() or "kplus" in sender.lower()
    
    def parse(self, email_text: str) -> Transaction | None:
        """Parse KBank email (MVP stub)."""
        logger.info("KBankParser.parse() - stub implementation")
        # TODO: Implement full KBank parser with:
        # - Normalizer
        # - Section selector (Thai/English)
        # - Field extractor
        # - Canonical mapper
        # - Transaction detector
        # - Status detector
        # - Direction detector
        # - Validator
        return None
