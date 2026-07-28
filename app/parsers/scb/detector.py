"""SCB Sender Detector - decide whether an email originates from SCB."""


def is_scb_sender(sender: str) -> bool:
    """Return True if the sender looks like a Siam Commercial Bank address."""
    sender_lower = (sender or "").lower()
    return "scb" in sender_lower or "scbeasynet@scb.co.th" in sender_lower
