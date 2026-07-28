"""Krungsri Sender Detector - decide whether an email originates from Krungsri."""


def is_krungsri_sender(sender: str) -> bool:
    """Return True if the sender looks like a Krungsri (Bank of Ayudhya) address."""
    sender_lower = (sender or "").lower()
    return "krungsri" in sender_lower or "admin@krungsri.com" in sender_lower
