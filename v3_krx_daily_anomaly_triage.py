"""KRX daily numeric observation triage. OFFLINE; strictly descriptive only.

Not a classifier for suspensions, delistings, splits or market sessions.
Source data may use '-' or '0' for no price, but those values do not prove
WHY the price is missing. Never backfill or synthesize an OHLC observation.
"""
from __future__ import annotations

from official_probe.clients import ProbeError
from official_probe.validation import _numeric

PRICE_FIELDS = ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC")
PRICE_MISSING = ("missing_price_field", "non_numeric_price",
                 "zero_or_negative_price", "ohlc_range_inconsistent",
                 "missing_volume_field", "non_numeric_volume")
REASONS = ("valid_numeric_observation",) + PRICE_MISSING


def new_counts():
    return {reason: 0 for reason in REASONS}


def classify_row(row):
    """Return exactly (allowlisted_reason, zero_aggregate_volume_observed).

    A missing field, literal '-', zero price, and out-of-bounds OHLC are
    separate, but NONE is labelled as a corporate action or trading halt.
    """
    if not isinstance(row, dict):
        raise ValueError("non_object_market_row")
    numbers = {}
    for name in PRICE_FIELDS:
        raw = row.get(name)
        if raw is None or type(raw) is str and raw.strip() in ("", "-"):
            return "missing_price_field", False
        try:
            numbers[name] = _numeric(raw, name)
        except ProbeError:
            return "non_numeric_price", False
        if numbers[name] <= 0:
            return "zero_or_negative_price", False
    op = numbers["TDD_OPNPRC"]
    hi = numbers["TDD_HGPRC"]
    lo = numbers["TDD_LWPRC"]
    close = numbers["TDD_CLSPRC"]
    if lo > min(op, close) or hi < max(op, close) or lo > hi:
        return "ohlc_range_inconsistent", False
    vol = row.get("ACC_TRDVOL")
    if vol is None or type(vol) is str and vol.strip() in ("", "-"):
        return "missing_volume_field", False
    try:
        accumulated = _numeric(vol, "ACC_TRDVOL")
    except ProbeError:
        return "non_numeric_volume", False
    return "valid_numeric_observation", accumulated == 0


def validate_quality_counts(counts, total_rows, zero_volume_rows):
    if (not isinstance(counts, dict) or set(counts) != set(REASONS)
            or any(type(counts[k]) is not int or not 0 <= counts[k] <= 20000
                   for k in REASONS)
            or type(total_rows) is not int or not 0 < total_rows <= 20000
            or sum(counts.values()) != total_rows
            or type(zero_volume_rows) is not int
            or not 0 <= zero_volume_rows <= counts["valid_numeric_observation"]):
        raise ValueError("invalid_anomaly_count_integrity")
    return True
