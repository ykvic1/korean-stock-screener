"""Stable, non-sensitive taxonomy for ALL-current-stock v2.2 OHLCV failures.

Vendor exceptions, symbols, URLs, API keys, and raw price bars are never
published in error labels. Each attempt has one disposition as before.
This is an EXPLANATION instrument, not a way to bypass quality checks.
"""
from __future__ import annotations

# Finite allowlist of codes. Use descriptive remediation categories, not
# fragile string matching of third-party exception messages.
REASONS = frozenset({
    "empty_price_source",
    "no_price_session_by_asof",
    "insufficient_history",  # reserved for the separate exclusion bucket
    "missing_ohlcv_columns",
    "unparseable_ohlcv_numeric",
    "nonfinite_ohlcv",
    "invalid_latest_close",
    "invalid_latest_volume",
    "stale_price_session",
    "upstream_value_error",
    "upstream_timeout",
    "upstream_connection_error",
    "upstream_rate_limit_or_http_error",
    "upstream_schema_or_parse_error",
    "other_price_source_error",
})

class PriceHistoryIssue(ValueError):
    """Safe, locally observed price quality failure (no vendor message)."""
    def __init__(self, reason: str):
        if type(reason) is not str or reason not in REASONS:
            raise ValueError("invalid_safe_price_reason")
        self.reason = reason
        super().__init__(reason)


def safe_price_failure_reason(exc: Exception) -> str:
    """One stable, redacted code from provider or local validation failure.

    An InsufficientHistory exception must be classified by the caller into
    the distinct excluded_history bucket, *not* this price-failure function.
    """
    if isinstance(exc, PriceHistoryIssue):
        return exc.reason
    if isinstance(exc, TimeoutError):
        return "upstream_timeout"
    if isinstance(exc, ConnectionError):
        return "upstream_connection_error"
    if isinstance(exc, (KeyError, TypeError, UnicodeError)):
        return "upstream_schema_or_parse_error"
    # No repr(), no str(exc), no dynamic class names.
    name = type(exc).__name__
    if name in ("HTTPError", "TooManyRequests", "RateLimitError"):
        return "upstream_rate_limit_or_http_error"
    if isinstance(exc, ValueError):
        return "upstream_value_error"
    return "other_price_source_error"
