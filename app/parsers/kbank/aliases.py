"""KBank field label aliases - map raw Thai/English labels to canonical field names."""

CANONICAL_ALIASES: dict[str, str] = {
    # Date / time
    "transaction date": "transaction_date",
    "date": "transaction_date",
    "วันที่ทำรายการ": "transaction_date",
    "วันที่": "transaction_date",
    "transaction time": "transaction_time",
    "time": "transaction_time",
    "เวลาทำรายการ": "transaction_time",
    "เวลา": "transaction_time",

    # Amount / fee / balance
    "amount": "amount",
    "จำนวนเงิน": "amount",
    "fee": "fee",
    "transaction fee": "fee",
    "ค่าธรรมเนียม": "fee",
    "available balance": "balance",
    "balance": "balance",
    "ยอดเงินคงเหลือ": "balance",
    "ยอดคงเหลือ": "balance",

    # Accounts / parties
    "from account": "from_account",
    "from": "from_account",
    "จากบัญชี": "from_account",
    "บัญชีต้นทาง": "from_account",
    "to account": "to_account",
    "to": "to_account",
    "ไปยังบัญชี": "to_account",
    "บัญชีปลายทาง": "to_account",
    "to name": "counterparty",
    "recipient": "counterparty",
    "payee": "counterparty",
    "merchant": "counterparty",
    "ชื่อผู้รับ": "counterparty",
    "ผู้รับเงิน": "counterparty",
    "ร้านค้า": "counterparty",
    "biller": "counterparty",
    "ผู้ให้บริการ": "counterparty",

    # Reference / status / channel
    "reference": "reference",
    "reference no": "reference",
    "reference number": "reference",
    "ref no": "reference",
    "หมายเลขอ้างอิง": "reference",
    "เลขที่อ้างอิง": "reference",
    "status": "status",
    "สถานะ": "status",
    "channel": "channel",
    "ช่องทาง": "channel",
}


def to_canonical(raw_label: str) -> str | None:
    """Normalize a raw field label and map it to a canonical field name, or None."""
    key = raw_label.strip().strip(":：").strip().lower()
    return CANONICAL_ALIASES.get(key)
