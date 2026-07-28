"""LH Bank Sender Detector - decide whether an email originates from LH Bank."""


def is_lhbank_sender(sender: str) -> bool:
    """Return True if the sender looks like a Land and House Bank address."""
    sender_lower = (sender or "").lower()
    return "lhbank" in sender_lower or "lhbyou@lhbank.co.th" in sender_lower
