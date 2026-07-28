"""LH Bank field label aliases - map raw Thai labels to canonical field names."""

CANONICAL_ALIASES: dict[str, str] = {
    "วันเวลา": "occurred_at",
    "อุปกรณ์": "device",
    "จาก": "from_account_info",
    "ไปยัง": "counterparty",
    "หมายเลขร้านค้า1": "merchant_code_1",
    "เลขที่อ้างอิง2": "reference_2",
    "จำนวนเงิน (บาท)": "amount",
    "ค่าธรรมเนียม (บาท)": "fee",
    "บันทึกช่วยจำ": "memo",
}


def to_canonical(raw_label: str) -> str | None:
    """Map a raw Thai field label to a canonical field name, or None if unknown."""
    key = raw_label.strip().strip(":：").strip()
    return CANONICAL_ALIASES.get(key)
