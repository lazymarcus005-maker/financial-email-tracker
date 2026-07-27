"""KBank field label aliases - map raw Thai/English labels to canonical field names."""

import re

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
    "transaction amount": "amount",
    "transfer amount": "amount",
    "payment amount": "amount",
    "bill amount": "amount",
    "bill payment amount": "amount",
    "paid amount": "amount",
    "จำนวนเงิน": "amount",
    "ยอดเงิน": "amount",
    "จำนวนเงินโอน": "amount",
    "ยอดเงินโอน": "amount",
    "จำนวนเงินที่โอน": "amount",
    "ยอดเงินที่โอน": "amount",
    "จำนวนเงินที่ชำระ": "amount",
    "ยอดเงินที่ชำระ": "amount",
    "ยอดชำระ": "amount",
    "fee": "fee",
    "transaction fee": "fee",
    "transfer fee": "fee",
    "payment fee": "fee",
    "bill payment fee": "fee",
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
    "ref": "reference",
    "หมายเลขอ้างอิง": "reference",
    "เลขที่อ้างอิง": "reference",
    "status": "status",
    "สถานะ": "status",
    "channel": "channel",
    "ช่องทาง": "channel",
}


_PARENTHETICAL_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*")
_TRAILING_NUMBER_WORD_RE = re.compile(r"\s+(no|number|no\.|number\.)$")


def _normalize_label(raw_label: str) -> str:
    key = raw_label.strip().strip(":：").strip().lower()
    key = _PARENTHETICAL_RE.sub(" ", key)
    key = key.replace("บาท", " ")
    key = re.sub(r"\b(thb|baht)\b", " ", key)
    key = re.sub(r"[._/-]+", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    key = _TRAILING_NUMBER_WORD_RE.sub("", key).strip()
    return key


def to_canonical(raw_label: str) -> str | None:
    """Normalize a raw field label and map it to a canonical field name, or None."""
    key = _normalize_label(raw_label)
    return CANONICAL_ALIASES.get(key)
