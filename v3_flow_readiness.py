"""v3 research-only investor-flow health/coverage audit.

Never upgrade Npay HTML (or an unproven KRX-selected view) into a verified
regular-session/investor-flow feed. Missing values stay unknown, not zero.
No network, orders, publication, investment scores, or live supplier changes.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import re

CODE = re.compile(r"[0-9]{6}\Z")
PERIODS = (20, 60, 120, 250)
MAX_DAILY_BYTES = 12_000_000
MAX_CANDIDATE_ROWS = 250


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid Korean-market date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Invalid Korean-market date") from None


def diagnose_v2_snapshot(payload):
    """Classify *actual errors* separately from breaker-induced deferrals."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Invalid v2 snapshot")
    asof = _day(payload.get("date")).isoformat()
    errors = payload.get("collection_errors")
    confirmed = payload.get("flow_confirmed_count")
    count = payload.get("candidate_count")
    attempted = payload.get("flow_attempted_count")
    if (not isinstance(errors, list) or len(errors) > 2000
            or type(payload.get("error_count")) is not int
            or payload["error_count"] != len(errors)
            or type(confirmed) is not int or confirmed < 0
            or type(count) is not int or count <= 0 or confirmed > count
            or type(attempted) is not int or attempted < 0 or attempted > 2000):
        raise ValueError("Inconsistent collection counts")
    buckets = {
        "npay_exchange_control_locator_timeout": 0,
        "deferred_timeout_circuit_breaker": 0,
        "deferred_collection_time_budget": 0,
        "npay_other_supplier_failure": 0,
        "other_flow_failure": 0,
    }
    flow_errors = 0
    observed_codes = set()
    for item in errors:
        if (not isinstance(item, list) or len(item) != 3
                or not isinstance(item[0], str) or not CODE.fullmatch(item[0])
                or not isinstance(item[1], str) or not isinstance(item[2], str)
                or len(item[2]) > 2500):
            raise ValueError("Malformed source error row")
        if item[1] != "기관·외국인 수급":
            continue
        if item[0] in observed_codes:
            raise ValueError("Duplicate flow error for one symbol")
        observed_codes.add(item[0])
        flow_errors += 1
        msg = item[2]
        if ("TraderTrend_trader-trend-toolbar-exchange" in msg
                and "Timeout" in msg):
            category = "npay_exchange_control_locator_timeout"
        elif "deferred: consecutive supplier timeouts" in msg:
            category = "deferred_timeout_circuit_breaker"
        elif "deferred: flow collection time budget exhausted" in msg:
            category = "deferred_collection_time_budget"
        elif "Npay:" in msg:
            category = "npay_other_supplier_failure"
        else:
            category = "other_flow_failure"
        buckets[category] += 1

    # The 205 deferred rows are NOT 205 independent Npay failures.
    directly_observed_locator_timeouts = buckets[
        "npay_exchange_control_locator_timeout"]
    breaker_deferred = buckets["deferred_timeout_circuit_breaker"]
    if flow_errors > attempted + breaker_deferred + buckets[
            "deferred_collection_time_budget"]:
        # Error rows from a different workflow/source mix need separate audit.
        raise ValueError("Flow error census inconsistent with attempted requests")

    return {
        "schema": "v3-flow-supplier-health-0.1",
        "asof": asof,
        "daily_candidates": count,
        "confirmed_flow_candidates_in_existing_snapshot": confirmed,
        "actual_supplier_attempts_reported": attempted,
        "flow_error_rows": flow_errors,
        "root_cause_classes": buckets,
        "actual_observed_npay_exchange_locator_timeouts":
            directly_observed_locator_timeouts,
        "circuit_breaker_follow_on_deferrals": breaker_deferred,
        "missing_flow_is_zero_net_shares": False,
        "verified_source_payload_available": False,
        "n_pay_dom_structure_independently_rechecked": False,
        "verified_regular_session_investor_flows": False,
        "ias_investor_flow_component": None,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
        "diagnosis": (
            "observed_npay_exchange_control_timeout_plus_breaker"
            if directly_observed_locator_timeouts and breaker_deferred
            else "no_confirmed_root_cause_or_mixed_supplier_errors"),
        "next_required_step": (
            "Use a bounded read-only browser observation to verify the current "
            "Npay exchange selector/table/pagination; do not guess DOM locators "
            "or enable flow scoring from a single vendor"),
    }


def examine_daily_flow_candidate(code, asof, rows, *, max_age_days=6):
    """Numerical observation only, never KRX/session certified.

    A zero signed net share count is a real observation only when present as
    integer 0 in a row. Null/missing fields are never inferred as zero.
    """
    if not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError("Invalid stock code")
    cutoff = _day(asof)
    if (not isinstance(rows, list) or len(rows) > MAX_CANDIDATE_ROWS
            or type(max_age_days) is not int or not 0 <= max_age_days <= 14):
        raise ValueError("Invalid bounded flow candidate input")
    seen = set()
    records = []
    for row in rows:
        if (not isinstance(row, dict)
                or row.get("code") != code
                or row.get("regular_session_verified") is True
                or row.get("confirmed_scoring_allowed") is True):
            raise ValueError("Unverifiable or promoted source row")
        d = _day(row.get("trade_date"))
        if d > cutoff or d in seen:
            raise ValueError("Future or duplicate investor flow day")
        seen.add(d)
        # Reject untyped zero, NaN, missing rows, strings, and booleans.
        inst, foreign = row.get("inst_net_shares"), row.get("foreign_net_shares")
        if type(inst) is not int or type(foreign) is not int:
            raise ValueError("Missing/non-integer investor shares")
        if abs(inst) > 10**13 or abs(foreign) > 10**13:
            raise ValueError("Unrealistic or corrupt signed share count")
        records.append((d, inst, foreign))
    records.sort(key=lambda v: v[0], reverse=True)
    latest = records[0][0] if records else None
    fresh = latest is not None and (cutoff - latest).days <= max_age_days
    period_views = {}
    for period in PERIODS:
        available = fresh and len(records) >= period
        values = records[:period] if available else []
        period_views[str(period)] = {
            "requested_trading_observations": period,
            "available_numeric_observations": min(len(records), period),
            "numerical_window_complete": bool(available),
            "foreign_net_shares": sum(v[2] for v in values) if available else None,
            "inst_net_shares": sum(v[1] for v in values) if available else None,
            "foreign_positive_days": sum(v[2] > 0 for v in values) if available else None,
            "inst_positive_days": sum(v[1] > 0 for v in values) if available else None,
            "official_exchange_and_session_verified": False,
            "ias_score_eligible": False,
        }
    return {
        "schema": "v3-uncertified-investor-flow-observation-0.1",
        "code": code, "asof": cutoff.isoformat(),
        "source_class": "unverified_external_or_legacy_observation",
        "observed_numeric_rows": len(records),
        "latest_row_date": latest.isoformat() if latest else None,
        "freshness_observed": bool(fresh),
        "trading_calendar_and_session_independently_verified": False,
        "market_participant_identity_verified": False,
        "independent_source_crosscheck_passed": False,
        "windows": period_views,
        "missing_values_imputed_as_zero": False,
        "investor_flow_scoring_allowed": False,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    }


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Read-only v3 investor-flow failure census")
    p.add_argument("--daily", default="docs/data.json")
    args = p.parse_args(argv)
    path = Path(args.daily)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_DAILY_BYTES:
        p.error("Existing daily file missing or too large")
    try:
        result = diagnose_v2_snapshot(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError):
        p.error("Unable to validate existing published daily snapshot")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
