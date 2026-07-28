"""SCB Validator - check required fields and compute parse status/confidence."""

REQUIRED_FIELDS = ("amount", "occurred_at", "to_bank", "to_account")
CRITICAL_FIELDS = ("amount", "occurred_at")


def _is_missing(value) -> bool:
    return value is None or value == ""


def validate(canonical) -> tuple[str, float, list[str]]:
    """Validate parsed fields, returning (parse_status, parse_confidence, warnings)."""
    warnings = list(canonical.warnings)

    missing = [f for f in REQUIRED_FIELDS if _is_missing(getattr(canonical, f))]
    critical_missing = [f for f in CRITICAL_FIELDS if f in missing]

    if critical_missing:
        warnings.append(f"Missing critical fields: {', '.join(critical_missing)}")
        return "failed", 0.0, warnings

    if missing:
        warnings.append(f"Missing required fields: {', '.join(missing)}")
        return "partial", 0.6, warnings

    if not warnings:
        return "complete", 1.0, warnings

    return "partial", 0.8, warnings
