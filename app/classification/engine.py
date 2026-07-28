"""Category engine - Manual > History > Rule > AI > Uncategorized."""

import logging
from enum import Enum

import aiosqlite

from app.classification import ai, history

logger = logging.getLogger(__name__)


class CategorySource(str, Enum):
    """Source of category assignment."""
    MANUAL = "manual"
    HISTORY = "history"
    RULE = "rule"
    AI = "ai"
    UNCATEGORIZED = "uncategorized"


DEFAULT_RULES = {
    # counterparty substring (lowercase) -> category
    "shopee": "Shopping",
    "lazada": "Shopping",
    "netflix": "Subscription",
    "spotify": "Subscription",
}


class CategoryEngine:
    """Categorize transactions based on priority:
    1. Manual override
    2. History (same counterparty seen before)
    3. Rule-based mapping
    4. AI (optional, if enabled)
    5. Uncategorized
    """

    def __init__(
        self,
        ai_enabled: bool = False,
        rules: dict[str, str] | None = None,
        ollama_base_url: str = "http://localhost:11434",
        ollama_model: str = "qwen3:1.7b",
    ):
        self.ai_enabled = ai_enabled
        self.rules = rules if rules is not None else dict(DEFAULT_RULES)
        self.ollama_base_url = ollama_base_url
        self.ollama_model = ollama_model

    def _match_rule(self, counterparty: str | None) -> str | None:
        if not counterparty:
            return None
        counterparty_lower = counterparty.lower()
        for key, category in self.rules.items():
            if key in counterparty_lower:
                return category
        return None

    async def categorize(
        self,
        db: aiosqlite.Connection,
        transaction: dict,
        manual_override: str | None = None,
        owner_user_id: int | None = None,
    ) -> tuple[str, str]:
        """Categorize a transaction. Returns (category, source)."""
        if manual_override:
            return manual_override, CategorySource.MANUAL.value

        counterparty = transaction.get("counterparty")

        history_category = await history.lookup(db, counterparty, owner_user_id=owner_user_id)
        if history_category:
            return history_category, CategorySource.HISTORY.value

        rule_category = self._match_rule(counterparty)
        if rule_category:
            return rule_category, CategorySource.RULE.value

        if self.ai_enabled:
            ai_category = await ai.categorize(
                transaction, base_url=self.ollama_base_url, model=self.ollama_model
            )
            if ai_category:
                return ai_category, self.ollama_model

        return "Uncategorized", CategorySource.UNCATEGORIZED.value
