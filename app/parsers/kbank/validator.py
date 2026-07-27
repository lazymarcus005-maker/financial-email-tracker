"""KBank Validator - check required fields and compute parse status/confidence."""

REQUIRED_FIELDS = ("transaction_date", "amount")


def validate(canonical, attrs) -> tuple[str, float, list[str]]:
    """Validate parsed fields, returning (parse_status, parse_confidence, warnings)."""
    warnings = list(canonical.warnings)

    missing = [f for f in REQUIRED_FIELDS if getattr(canonical, f) is None]
    if missing:
        warnings.append(f"Missing required fields: {', '.join(missing)}")
        return "failed", 0.0, warnings

    if attrs.transaction_type == "unknown":
        warnings.append("Could not determine transaction_type")
    if attrs.status == "unknown":
        warnings.append("Could not determine status")
    if attrs.direction == "unknown":
        warnings.append("Could not determine direction")

    if not warnings:
        return "complete", 1.0, warnings

    unknown_count = sum(1 for v in (attrs.transaction_type, attrs.status, attrs.direction) if v == "unknown")
    confidence = max(0.4, 1.0 - 0.2 * unknown_count - 0.05 * len(canonical.warnings))
    return "partial", round(confidence, 2), warnings
