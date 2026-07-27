"""AI-assisted categorization via a local Ollama model. Best-effort - any failure falls back to None."""

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0

CATEGORY_CHOICES = [
    "Shopping",
    "Transfer",
    "Subscription",
    "Food",
    "Bills",
    "Transport",
    "Entertainment",
    "Other",
]

_PROMPT_TEMPLATE = """You categorize a bank transaction into exactly one of these categories: {choices}.
Reply with only the category name, nothing else.

Transaction type: {transaction_type}
Direction: {direction}
Merchant/counterparty: {counterparty}
Amount: {amount}
"""


async def categorize(
    transaction: dict,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Ask Ollama to categorize a transaction. Returns a category from CATEGORY_CHOICES, or None on failure."""
    prompt = _PROMPT_TEMPLATE.format(
        choices=", ".join(CATEGORY_CHOICES),
        transaction_type=transaction.get("transaction_type", "unknown"),
        direction=transaction.get("direction", "unknown"),
        counterparty=transaction.get("counterparty") or "unknown",
        amount=transaction.get("amount", "unknown"),
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"AI categorization failed, falling back: {e}")
        return None

    raw_answer = (data.get("response") or "").strip()
    return _match_category(raw_answer)


def _match_category(raw_answer: str) -> str | None:
    normalized = raw_answer.strip().strip(".").lower()
    for choice in CATEGORY_CHOICES:
        if choice.lower() == normalized or choice.lower() in normalized:
            return choice
    logger.warning(f"AI returned an unrecognized category: {raw_answer!r}")
    return None
