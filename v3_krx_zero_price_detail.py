"""Safe aggregates for raw ZERO OHLC observations from KRX history.

Never label a row as suspended, delisted, non-tradable or split-adjusted.
Never emit stock codes, price values, response bodies or AUTH_KEY.
"""
from __future__ import annotations

from official_probe.clients import ProbeError
from official_probe.validation import _numeric

OHLC = {
    "TDD_OPNPRC": "zero_open_rows",
    "TDD_HGPRC": "zero_high_rows",
    "TDD_LWPRC": "zero_low_rows",
    "TDD_CLSPRC": "zero_close_rows",
}
DETAIL_KEYS = (
    "zero_price_rows", "zero_open_rows", "zero_high_rows",
    "zero_low_rows", "zero_close_rows", "multiple_zero_ohlc_rows",
    "zero_price_with_zero_accumulated_volume",
    "zero_price_with_positive_accumulated_volume",
    "zero_price_with_unparseable_accumulated_volume",
)


def empty_zero_details():
    return {key: 0 for key in DETAIL_KEYS}


def observe_zero_price_row(row, aggregate):
    """Call ONLY after classify_row returned zero_or_negative_price.

    Current KRX _numeric accepts unsigned integers only: the row's numeric
    nonpositive observation is zero. Parse EACH OHLC rather than stopping at
    the first zero as in the baseline classifier.
    """
    if not isinstance(row, dict) or not isinstance(aggregate, dict):
        raise ValueError("invalid_zero_price_inspection")
    hit = []
    for field, output_key in OHLC.items():
        try:
            if _numeric(row.get(field), field) == 0:
                hit.append(output_key)
        except ProbeError:
            continue
    if not hit:
        raise ValueError("zero_price_reason_without_zero_field")
    aggregate["zero_price_rows"] += 1
    if len(hit) >= 2:
        aggregate["multiple_zero_ohlc_rows"] += 1
    for key in hit:
        aggregate[key] += 1
    try:
        vol = _numeric(row.get("ACC_TRDVOL"), "ACC_TRDVOL")
    except ProbeError:
        aggregate["zero_price_with_unparseable_accumulated_volume"] += 1
    else:
        if vol == 0:
            aggregate["zero_price_with_zero_accumulated_volume"] += 1
        else:
            aggregate["zero_price_with_positive_accumulated_volume"] += 1


def validate_zero_details(summary, zero_reason_count):
    if (not isinstance(summary, dict) or set(summary) != set(DETAIL_KEYS)
            or type(zero_reason_count) is not int
            or not 0 <= zero_reason_count <= 20000
            or any(type(summary[k]) is not int or
                   not 0 <= summary[k] <= zero_reason_count
                   for k in DETAIL_KEYS)):
        raise ValueError("zero_detail_fields_invalid")
    n = summary["zero_price_rows"]
    if n != zero_reason_count:
        raise ValueError("zero_detail_rows_disagree")
    if sum(summary[k] for k in (
            "zero_price_with_zero_accumulated_volume",
            "zero_price_with_positive_accumulated_volume",
            "zero_price_with_unparseable_accumulated_volume")) != n:
        raise ValueError("zero_detail_volume_accounting_disagree")
    field_totals = sum(summary[k] for k in OHLC.values())
    if n > field_totals or field_totals > 4 * n:
        raise ValueError("zero_detail_field_accounting_disagree")
    if n and not 0 <= summary["multiple_zero_ohlc_rows"] <= n:
        raise ValueError("zero_detail_multiple_fields_invalid")
    return True
