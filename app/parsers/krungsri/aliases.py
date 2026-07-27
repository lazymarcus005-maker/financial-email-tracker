"""Krungsri field label aliases - map raw Thai labels to canonical field names."""

CANONICAL_ALIASES: dict[str, str] = {
    "ผลการทำรายการ": "result",
    "ประเภทรายการ": "transaction_type",
    "หักจากบัญชี": "account_name",
    "ผู้รับชำระเงิน": "counterparty",
    "จำนวนเงิน (บาท)": "amount",
    "ค่าธรรมเนียม (บาท)": "fee",
    "รหัสร้านค้า": "merchant_code",
    "รหัสอ้างอิงร้านค้า": "merchant_reference",
    "รหัสธุรกรรม": "transaction_code",
    "อ้างอิง 1": "reference_1",
    "อ้างอิง 2": "reference_2",
    "หมายเลขอ้างอิง": "reference_number",
    "วัน-เวลาที่ทำรายการ": "occurred_at",
    "บันทึกช่วยจำ": "memo",
}


def to_canonical(raw_label: str) -> str | None:
    """Map a raw Thai field label to a canonical field name, or None if unknown."""
    key = raw_label.strip().strip(":：").strip()
    return CANONICAL_ALIASES.get(key)
