"""Offline, research-only IAS investor-flow ledger: no live access or scoring.

Observe foreign/institutional/pension rows over 20/60/120/250 recorded dates.
Pension is a SUBSET within the institution total, not an extra buyer to add.
No calendar, share-adjustment, institution definition or provider is certified
merely by a session label, digest or boolean in caller-provided data.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json

PERIODS = (20, 60, 120, 250)
# Agreed weights are INTERNAL to IAS 'accumulated_net_buy' (25/100),
# NOT multipliers applied directly to shares or to the IAS 25pt pillar.
PERIOD_WEIGHTS = {20: 15, 60: 25, 120: 30, 250: 30}
SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"
MAX_ROWS = 3500
MAX_ABS_NET_SHARES = 10**13
FIELDS = (
    "foreign_net_shares",
    "institution_total_net_shares",
    "pension_net_shares",
)


class FlowLedgerError(ValueError):
    """Only fixed error strings. Provider values must not leak to CI logs."""


def _date(value):
    if type(value) is not str or len(value) != 10:
        raise FlowLedgerError("invalid_observation_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise FlowLedgerError("invalid_observation_date") from None
    if parsed.isoformat() != value:
        raise FlowLedgerError("invalid_observation_date")
    return parsed


def _code(value, count):
    return (type(value) is str and len(value) == count and value.isascii()
            and value.isdigit())


def audit_daily_flow(ledger, *, asof):
    """Compute noncertifying cumulative net-share observations (never IAS score).

    An explicitly missing field stays unknown for the full affected window,
    while an observed integer 0 is a real zero observation. No calendar-date
    subtraction is used in place of a counted trading-session window.
    """
    cutoff = _date(asof)
    if not isinstance(ledger, dict):
        raise FlowLedgerError("invalid_ledger")
    if not _code(ledger.get("code"), 6) or not _code(ledger.get("issuer_id"), 8):
        raise FlowLedgerError("invalid_stock_identity")
    if ledger.get("market") not in ("KOSPI", "KOSDAQ"):
        raise FlowLedgerError("invalid_market")
    if ledger.get("institution_definition") != "total_includes_pension":
        raise FlowLedgerError("unverified_or_ambiguous_institution_category")
    if ledger.get("pension_definition") != "pension_etc_subcategory":
        raise FlowLedgerError("unverified_or_ambiguous_pension_category")
    rows = ledger.get("observations")
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise FlowLedgerError("invalid_rows")
    prev = None
    validated = []
    for row in rows:
        if not isinstance(row, dict):
            raise FlowLedgerError("invalid_row")
        d = _date(row.get("date"))
        if d > cutoff or (prev is not None and d <= prev):
            raise FlowLedgerError("future_duplicate_or_unordered_flow_date")
        if row.get("session") != SESSION:
            raise FlowLedgerError("unsupported_session_label")
        if row.get("market") != ledger["market"]:
            raise FlowLedgerError("mixed_market_history")
        for field in FIELDS:
            if field not in row:
                raise FlowLedgerError("missing_required_flow_field")
            value = row[field]
            if value is not None and (
                    type(value) is not int or abs(value) > MAX_ABS_NET_SHARES):
                raise FlowLedgerError("invalid_net_share_amount")
        validated.append(row)
        prev = d
    periods = {}
    for length in PERIODS:
        tail = validated[-length:] if len(validated) >= length else []
        enough = len(tail) == length
        categories = {}
        for field in FIELDS:
            if not enough or any(r[field] is None for r in tail):
                categories[field] = {
                    "cumulative_net_shares": None,
                    "observations": length if enough else len(validated),
                    "data_complete": False,
                }
            else:
                categories[field] = {
                    "cumulative_net_shares": sum(r[field] for r in tail),
                    "observations": length, "data_complete": True,
                }
        periods[str(length)] = {
            "requested_observed_sessions": length,
            "agreed_accumulated_net_buy_weight_pct": PERIOD_WEIGHTS[length],
            "categories": categories,
            "foreign_plus_institution_plus_pension_total": None,
            "observed_dates_count": len(tail),
            "independently_authenticated_calendar": False,
        }
    canonical = {
        "code": ledger["code"], "issuer_id": ledger["issuer_id"],
        "market": ledger["market"], "definitions": (
            ledger["institution_definition"], ledger["pension_definition"]),
        "observations": validated
    }
    digest = hashlib.sha256(json.dumps(
        canonical, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "schema": "v3-ias-flow-ledger-observation-0.1",
        "asof": cutoff.isoformat(), "latest_observed_date": prev.isoformat(),
        "code": ledger["code"], "issuer_id": ledger["issuer_id"],
        "observed_rows": len(validated), "stale_vs_asof": prev != cutoff,
        "periods": periods,
        "pension_is_subcategory_of_institution": True,
        "never_double_add_pension_to_institution": True,
        "net_buy_period_weights_sum": sum(PERIOD_WEIGHTS.values()),
        "weights_not_applied_to_unscaled_net_shares": True,
        "input_digest_for_change_detection": digest,
        "all_rows_issuer_identity_authenticated": False,
        "official_session_and_calendar_verified": False,
        "category_taxonomy_independently_verified": False,
        "shares_corporate_action_adjusted_and_verified": False,
        "accumulated_net_buy_normalized_ias_points": None,
        "ias_100_points": None,
        "v3_six_pillar_confirmed_points": None,
        "forecast_from_flow": None,
        "investor_flow_scoring_allowed": False,
        "confirmed_buy": False,
    }
