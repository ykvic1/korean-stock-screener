"""Independent *research-only* daily investor-share comparison: Npay vs pykrx.

Neither vendor is being certified as authoritative, permitted for automated
republication, or guaranteed to describe KRX 09:00-15:30 activity. Compare
identical per-day signed net shares without exposing raw values or scoring.
No network access in this module.
"""
from __future__ import annotations

from datetime import date
from numbers import Integral
from typing import Any

from v3_npay_numeric_probe import (
    CODE, MAX_ROWS, UnverifiedFlowShape, _parse_day, _shares,
    normalize_anonymous_npay_rows,
)

ASOF = "2026-10-16"
COLUMNS = ("기관합계", "외국인합계")


def _strict_trade_date(raw: Any) -> str:
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise ValueError("Invalid source trading date") from None
    if hasattr(raw, "date"):
        value = raw.date()
        if isinstance(value, date):
            return value.isoformat()
    if type(raw) is date:
        return raw.isoformat()
    raise ValueError("Unexpected index date type")


def _strict_int(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError("Boolean is not investor volume")
    # numpy integer scalars implement Integral with current dependencies
    if isinstance(raw, Integral):
        v = int(raw)
    elif type(raw) is float and raw.is_integer():
        v = int(raw)
    else:
        raise ValueError("Not a whole signed investor share count")
    if abs(v) > 10**13:
        raise ValueError("Corrupt signed share magnitude")
    return v


def from_pykrx_dataframe(frame: Any, code: str = CODE) -> list[dict]:
    """Frame supplied by ONE bounded pykrx query; never infer missing columns."""
    if code != CODE or frame is None or not hasattr(frame, "columns"):
        raise ValueError("Invalid stock-specific KRX-interface response")
    if not all(column in frame.columns for column in COLUMNS):
        raise ValueError("Missing official-style investor columns")
    if len(frame) < MAX_ROWS or len(frame) > 80:
        raise ValueError("Insufficient or unexpectedly oversized daily investor history")
    data = []
    for idx, row in frame.iterrows():
        data.append({
            "code": code, "trade_date": _strict_trade_date(idx),
            "inst_net_shares": _strict_int(row[COLUMNS[0]]),
            "foreign_net_shares": _strict_int(row[COLUMNS[1]]),
        })
    return data


def audit_research_pair(npay_payload: Any, alternate_daily: list[dict],
                        *, asof: str = ASOF) -> dict:
    """Compare two observed feeds date-by-date, never authorize IAS scores."""
    # Reuse the proven 20-row Npay numeric contract. Parsing can raise on
    # unverified source data; there is no guessing or missing-row fill.
    reviewed = normalize_anonymous_npay_rows(npay_payload, asof=asof)
    if not reviewed["windows"]["20"]["numerical_window_complete"]:
        raise ValueError("Npay 20 trading observations incomplete or stale")
    npay = {}
    for item in npay_payload:
        day = _parse_day(item["bizdate"])
        npay[day] = (_shares(item["organPureBuyQuant"]),
                     _shares(item["foreignerPureBuyQuant"]))

    if not isinstance(alternate_daily, list) or not (
            MAX_ROWS <= len(alternate_daily) <= 80):
        raise ValueError("Independent feed does not contain bounded history")
    other = {}
    for row in alternate_daily:
        if not isinstance(row, dict) or row.get("code") != CODE:
            raise ValueError("Invalid independent stock code")
        day = _strict_trade_date(row.get("trade_date"))
        if day > asof or day in other:
            raise ValueError("Future or duplicate independent trading date")
        values = (_strict_int(row.get("inst_net_shares")),
                  _strict_int(row.get("foreign_net_shares")))
        other[day] = values
    # Compare only the latest 20 alternate days. This is a strict ordered
    # trading-day consistency check, not a calendar-day approximation.
    reference_days = set(sorted(other, reverse=True)[:MAX_ROWS])
    common_days = sorted(set(npay) & reference_days)
    inst_match = sum(npay[d][0] == other[d][0] for d in common_days)
    foreign_match = sum(npay[d][1] == other[d][1] for d in common_days)
    same_dates = len(reference_days) == MAX_ROWS and reference_days == set(npay)
    full_agreement = (same_dates and inst_match == MAX_ROWS
                      and foreign_match == MAX_ROWS)
    status = (
        "twenty_days_exact_numeric_agreement_research_only"
        if full_agreement else
        "investor_quantities_mismatch"
        if same_dates else "trading_day_sets_differ"
    )
    return {
        "schema": "v3-independent-investor-flow-20d-audit-0.1",
        "status": status,
        "code": CODE,
        "asof": asof,
        "npay_numeric_days": len(npay),
        "alternate_numeric_days": len(reference_days),
        "aligned_trading_days": len(common_days),
        "identical_trading_day_sets": bool(same_dates),
        "institutional_exact_match_days": inst_match,
        "foreign_exact_match_days": foreign_match,
        "both_categories_exact_all_20": bool(full_agreement),
        "underlying_source": "Npay KRX-requested unverified trend",
        "independent_source": "pykrx KRX-interface reported investor-trend",
        "independent_licensed_official_exchange_api_verified": False,
        "regular_session_and_category_definitions_verified": False,
        "vendor_automated_reuse_permission_verified": False,
        "all_60_120_250_trading_day_windows_validated": False,
        "raw_numeric_values_emitted": False,
        "ias_flow_component": None,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    }
