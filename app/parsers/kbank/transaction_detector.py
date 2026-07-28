"""KBank Transaction Detector - classify transaction_type, direction, and status."""

from dataclasses import dataclass

# (transaction_type, English keywords, Thai keywords) - checked in priority order
_TYPE_RULES = [
    ("promptpay_transfer", ["promptpay"], ["พร้อมเพย์"]),
    ("bill_payment", ["bill payment", "pay bill"], ["ชำระบิล", "ชำระค่าบริการ"]),
    ("merchant_payment", ["payment", "purchase", "qr payment"], ["ชำระเงิน", "ซื้อสินค้า"]),
    ("topup", ["top up", "top-up", "topup"], ["เติมเงิน"]),
    ("atm_withdrawal", ["withdraw", "atm"], ["ถอนเงิน"]),
    ("deposit", ["deposit"], ["ฝากเงิน"]),
    ("bank_transfer", ["transfer"], ["โอนเงิน", "โอน"]),
]

_SUCCESS_KEYWORDS = ["success", "successful", "complete", "สำเร็จ", "เรียบร้อย"]
_FAILED_KEYWORDS = ["fail", "failed", "unsuccessful", "ไม่สำเร็จ", "ล้มเหลว"]
_PENDING_KEYWORDS = ["pending", "processing", "รอดำเนินการ", "อยู่ระหว่างดำเนินการ"]
_CANCELLED_KEYWORDS = ["cancel", "cancelled", "canceled", "ยกเลิก"]

_IN_KEYWORDS = ["received", "you have received", "transfer from", "โอนเข้า", "รับเงิน"]
_OUT_KEYWORDS = ["sent", "transfer to", "โอนออก", "จ่าย"]
_OUT_SUBJECT_KEYWORDS = [
    "result of funds transfer",
    "result of promptpay funds transfer",
]

# Transaction types whose direction is implied regardless of subject wording.
_DIRECTION_BY_TYPE = {
    "bill_payment": "out",
    "merchant_payment": "out",
    "atm_withdrawal": "out",
    "deposit": "in",
    "topup": "out",
}


@dataclass
class TransactionAttributes:
    transaction_type: str
    direction: str
    status: str


def _match_any(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in keywords)


def _match_type(haystack: str) -> str:
    for candidate_type, en_keywords, th_keywords in _TYPE_RULES:
        if _match_any(haystack, en_keywords) or _match_any(haystack, th_keywords):
            return candidate_type
    return "unknown"


def detect(subject: str, canonical, body_text: str = "") -> TransactionAttributes:
    """Classify a transaction's type, direction, and status.

    Primarily uses the email subject (KBank subjects are usually a clear
    headline like "Transfer Successful") and the canonical fields extracted
    from the body, falling back to a full-body keyword scan if the subject
    alone isn't conclusive.
    """
    transaction_type = _match_type(" ".join(filter(None, [subject, canonical.channel])))
    if transaction_type == "unknown" and body_text:
        transaction_type = _match_type(body_text)

    status_haystack = " ".join(filter(None, [subject, canonical.status]))
    if _match_any(status_haystack, _FAILED_KEYWORDS):
        status = "failed"
    elif _match_any(status_haystack, _CANCELLED_KEYWORDS):
        status = "cancelled"
    elif _match_any(status_haystack, _PENDING_KEYWORDS):
        status = "pending"
    elif _match_any(status_haystack, _SUCCESS_KEYWORDS):
        status = "success"
    else:
        status = "unknown"

    if _match_any(subject, _IN_KEYWORDS):
        direction = "in"
    elif _match_any(subject, _OUT_KEYWORDS) or _match_any(subject, _OUT_SUBJECT_KEYWORDS):
        direction = "out"
    elif transaction_type in _DIRECTION_BY_TYPE:
        direction = _DIRECTION_BY_TYPE[transaction_type]
    else:
        direction = "unknown"

    return TransactionAttributes(
        transaction_type=transaction_type,
        direction=direction,
        status=status,
    )
