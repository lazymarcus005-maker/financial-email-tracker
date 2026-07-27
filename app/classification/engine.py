"""Category engine - Rule-based + AI fallback."""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class CategorySource(str, Enum):
    """Source of category assignment."""
    MANUAL = "manual"
    HISTORY = "history"
    RULE = "rule"
    AI = "ai"
    UNCATEGORIZED = "uncategorized"


class CategoryEngine:
    """Categorize transactions based on priority:
    1. Manual override
    2. History (same counterparty)
    3. Rule-based mapping
    4. AI (optional, if enabled)
    5. Uncategorized
    """
    
    def __init__(self, ai_enabled: bool = False):
        self.ai_enabled = ai_enabled
        self.rules = {
            # Examples - load from config
            "shopee": "Shopping",
            "lazada": "Shopping",
            "netflix": "Subscription",
            "spotify": "Subscription",
        }
    
    def categorize(self, transaction: dict, manual_override: str | None = None) -> tuple[str, CategorySource]:
        """Categorize transaction.
        
        Returns: (category, source)
        """
        # 1. Manual override
        if manual_override:
            return manual_override, CategorySource.MANUAL
        
        # 2. History (lookup counterparty in DB)
        # TODO: Implement history lookup
        
        # 3. Rule-based
        counterparty = transaction.get("counterparty", "").lower()
        for key, category in self.rules.items():
            if key in counterparty:
                return category, CategorySource.RULE
        
        # 4. AI (if enabled)
        if self.ai_enabled:
            # TODO: Call AI categorizer
            pass
        
        # 5. Uncategorized
        return "Uncategorized", CategorySource.UNCATEGORIZED
