"""LINE Messaging API integration - send push messages, format the daily summary."""

import logging

import httpx

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
DEFAULT_TIMEOUT_SECONDS = 10.0

_KNOWN_EXPENSE_CATEGORIES = ["Shopping", "Transfer", "Subscription"]


async def send_message(
    user_id: str | None,
    text: str,
    channel_access_token: str | None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Push a text message to a LINE user. Returns True on success, False on any failure (logged, never raises)."""
    if not channel_access_token or not user_id:
        logger.warning("LINE send_message skipped: missing channel_access_token or user_id")
        return False

    headers = {"Authorization": f"Bearer {channel_access_token}", "Content-Type": "application/json"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": text}]}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(LINE_PUSH_URL, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"LINE send_message failed: {e}")
        return False

    logger.info(f"LINE message sent to {user_id}")
    return True


def format_daily_summary(data: dict) -> str:
    """Format the daily summary dict (from queries.get_daily_summary_data) into a LINE message."""
    expense_by_category: dict = data.get("expense_by_category", {})
    expense_total = sum(expense_by_category.values())
    other_total = sum(
        amount for cat, amount in expense_by_category.items() if cat not in _KNOWN_EXPENSE_CATEGORIES
    )

    lines = [
        f"📊 วันนี้ ({data['date']})",
        "",
        f"💰 รายรับ: ฿{data['income_total']:,.2f}",
        f"  • {data['income_count']} รายการ",
        "",
        f"💸 รายจ่าย: ฿{expense_total:,.2f}",
    ]
    for category in _KNOWN_EXPENSE_CATEGORIES:
        lines.append(f"  • {category}: ฿{expense_by_category.get(category, 0.0):,.2f}")
    lines.append(f"  • อื่นๆ: ฿{other_total:,.2f}")
    lines.extend(
        [
            "",
            f"⚠️  ยังไม่ได้แบ่งหมวดหมู่: {data['uncategorized_count']} รายการ",
            f"❌ Parse Error: {data['parse_error_count']} รายการ",
            "",
            f"Last Sync: {data.get('last_sync') or 'N/A'}",
        ]
    )
    return "\n".join(lines)
