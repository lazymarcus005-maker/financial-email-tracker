"""SCB field label aliases - map raw Thai labels to canonical field names."""

CANONICAL_ALIASES: dict[str, str] = {
    "ประเภทของรายการ": "transaction_type_label",
    "จาก ธนาคาร": "from_bank",
    "เบอร์บัญชี": "from_account",
    "ไปยัง ธนาคาร": "to_bank",
    "จำนวนเงิน": "amount",
    "วันและเวลาการทำรายการ": "occurred_at",
}


def to_canonical(raw_label: str) -> str | None:
    """Map a raw Thai field label to a canonical field name, or None if unknown."""
    key = raw_label.strip().strip(":：").strip()
    return CANONICAL_ALIASES.get(key)
