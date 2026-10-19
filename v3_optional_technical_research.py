"""Step 8: executable *partial* technical research from PRIVATE KRX observations.

No regular-session provenance, corporate-action adjustment, ticker-issuer
lifecycle or investment scoring is inferred. This uses Step 7's strictly
unverified aggregate volume field: no fake 'session' or 'volume' keys.
Detailed numeric signals stay in the calling LOCAL Python process only.
CLI emits aggregate availability counts, never symbols or source prices.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math

from v3_krx_private_price_bridge import (
    build_private_price_panels, PrivatePanelError, _targets)
from v3_krx_private_backfill_plan import (
    calendar_weekday_candidates, BackfillPlanError)
from v3_krx_session_candidates import candidate_dates, CandidateCalendarError

MA_WINDOWS = (5, 20, 60)
WEEK_WINDOWS = (10, 20, 120)
OPTIONAL = ("daily_ma5", "daily_ma20", "daily_ma60", "daily_rsi14",
            "close_return20_pct", "close_return60_pct",
            "prior20_support_low", "prior20_resistance_high",
            "weekly_ma10", "weekly_ma20", "weekly_ma120",
            "reported_acc_volume_ratio20", "reported_acc_obv_proxy20_pct",
            "reported_acc_obv_proxy60_pct")
MAX_ALLOWED_UNADJUSTED_CLOSE_JUMP = 0.35


class PartialTechnicalError(ValueError):
    """Fixed reason string only; never echo stock codes, paths or prices."""


def _empty(status, *, asof=None, observed=0, warning=None):
    return {
        "status": status, "asof": asof,
        "contiguous_ending_bars": observed, "contiguous_ending_weeks": 0,
        "available": [], "unavailable": list(OPTIONAL),
        "indicators": {name: None for name in OPTIONAL},
        "warning": warning,
        "official_regular_session_verified": False,
        "price_adjustments_verified": False,
        "issuer_identity_verified": False,
        "reported_acc_volume_is_regular_only": False,
        "all_values_unverified_research_only": True,
        "v3_score_allowed": False, "confirmed_buy": False,
    }


def _rsi14(closes):
    """Observed Wilder RSI, no future weekly close or invented history."""
    if len(closes) < 15:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    up = sum(max(0, x) for x in diffs[:14]) / 14
    down = sum(max(0, -x) for x in diffs[:14]) / 14
    for delta in diffs[14:]:
        up = (13 * up + max(0, delta)) / 14
        down = (13 * down + max(0, -delta)) / 14
    if down == 0:
        return 100.0 if up > 0 else 50.0
    return round(100 - 100 / (1 + up / down), 4)


def analyze_observed_panel(panel, archived_prefix):
    """Compute only suffix-consecutive price observations ending at saved EOD.

    Missing ticker rows / zero OHLC break the contiguous run. Split-like
    jumps greater than 35% also break unadjusted signal history, but cannot
    prove an actual split or warrant patching raw prices.
    """
    if not isinstance(panel, dict) or not isinstance(archived_prefix, list):
        raise PartialTechnicalError("invalid_private_analysis_input")
    if not archived_prefix:
        return _empty("no_saved_two_market_dates")
    last = archived_prefix[-1]
    bars = panel.get("observed_bars")
    if not isinstance(bars, list):
        raise PartialTechnicalError("invalid_private_panel_rows")
    by_date = {}
    for row in bars:
        if not isinstance(row, dict) or type(row.get("date")) is not str:
            raise PartialTechnicalError("invalid_private_price_row")
        day = row["date"]
        if day in by_date:
            raise PartialTechnicalError("duplicate_private_price_day")
        if row.get("source_scope") != "KRX_EOD_AGGREGATE_UNVERIFIED":
            raise PartialTechnicalError("unexpected_or_spoofed_source_scope")
        if row.get("regular_only_volume_verified") is not False:
            raise PartialTechnicalError("false_regular_session_assertion")
        for field in ("open", "high", "low", "close", "aggregate_acc_trdvol_observed"):
            v = row.get(field)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                raise PartialTechnicalError("invalid_private_price_numeric")
            if field != "aggregate_acc_trdvol_observed" and v <= 0:
                raise PartialTechnicalError("invalid_private_price_numeric")
        if not (row["low"] <= min(row["open"], row["close"])
                and row["high"] >= max(row["open"], row["close"])):
            raise PartialTechnicalError("private_price_range_invalid")
        by_date[day] = row
    if last not in by_date:
        return _empty("latest_saved_pair_missing_or_quarantined", asof=last)
    tail = []
    jump_detected = False
    for day in reversed(archived_prefix):
        current = by_date.get(day)
        if current is None:
            break
        if tail and abs(tail[-1]["close"] / current["close"] - 1) > MAX_ALLOWED_UNADJUSTED_CLOSE_JUMP:
            jump_detected = True
            break
        tail.append(current)
    tail.reverse()
    n = len(tail)
    if n == 0:
        return _empty("latest_saved_pair_missing_or_quarantined", asof=last)
    closes = [float(b["close"]) for b in tail]
    values = {name: None for name in OPTIONAL}
    for window in MA_WINDOWS:
        if n >= window:
            values[f"daily_ma{window}"] = round(sum(closes[-window:]) / window, 4)
    values["daily_rsi14"] = _rsi14(closes)
    for window in (20, 60):
        if n >= window + 1:
            values[f"close_return{window}_pct"] = round(
                100 * (closes[-1] / closes[-window-1] - 1), 4)
    if n >= 21:
        values["prior20_support_low"] = min(float(b["low"]) for b in tail[-21:-1])
        values["prior20_resistance_high"] = max(float(b["high"]) for b in tail[-21:-1])
        historic_vol = [b["aggregate_acc_trdvol_observed"] for b in tail[-21:-1]]
        avg = sum(historic_vol) / 20
        if avg > 0:
            values["reported_acc_volume_ratio20"] = round(
                tail[-1]["aggregate_acc_trdvol_observed"] / avg, 4)
    for window in (20, 60):
        if n >= window + 1:
            last_rows = tail[-window:]
            total = sum(b["aggregate_acc_trdvol_observed"] for b in last_rows)
            if total > 0:
                weighted = sum(
                    (1 if b["close"] > prev else -1 if b["close"] < prev else 0)
                    * b["aggregate_acc_trdvol_observed"]
                    for b, prev in zip(last_rows, closes[-window-1:-1]))
                values[f"reported_acc_obv_proxy{window}_pct"] = round(
                    100 * weighted / total, 4)
    weekly = []
    keys = []
    for b in tail:
        d = date.fromisoformat(b["date"])
        key = d.isocalendar()[:2]
        if not keys or keys[-1] != key:
            keys.append(key)
            weekly.append(float(b["close"]))
        else:
            weekly[-1] = float(b["close"])
    for window in WEEK_WINDOWS:
        if len(weekly) >= window:
            values[f"weekly_ma{window}"] = round(
                sum(weekly[-window:]) / window, 4)
    result = _empty(
        "partial_observed_research_only",
        asof=last, observed=n,
        warning="large_unadjusted_price_jump_in_older_history" if jump_detected else None)
    result["contiguous_ending_weeks"] = len(weekly)
    result["indicators"] = values
    result["available"] = [k for k, v in values.items() if v is not None]
    result["unavailable"] = [k for k, v in values.items() if v is None]
    return result


def run_optional_technical_research(*, start, end, private_db, targets,
                                    calendar_source="xkrx"):
    """Read an already consented private source, not a live collection job."""
    try:
        _targets(targets)
        bundled = build_private_price_panels(
            start=start, end=end, private_db=private_db,
            targets=targets, calendar_source=calendar_source)
        weekdays = calendar_weekday_candidates(start, end)
        candidate = candidate_dates(weekdays, source=calendar_source)
    except (PrivatePanelError, BackfillPlanError, CandidateCalendarError):
        raise PartialTechnicalError("private_source_or_calendar_unavailable") from None
    archived_n = bundled["summary"]["archived_two_market_pairs"]
    candidate_days = candidate["dates"]
    if type(archived_n) is not int or not 0 <= archived_n <= len(candidate_days):
        raise PartialTechnicalError("archive_candidate_count_inconsistent")
    saved_dates = candidate_days[:archived_n]
    results = {}
    c = Counter()
    for key, panel in bundled["panels"].items():
        checked = analyze_observed_panel(panel, saved_dates)
        results[key] = checked
        c["targets_with_any_daily_indicator"] += bool(
            any(x.startswith("daily_") for x in checked["available"]))
        c["targets_with_daily_ma20"] += "daily_ma20" in checked["available"]
        c["targets_with_daily_ma60"] += "daily_ma60" in checked["available"]
        c["targets_with_rsi14"] += "daily_rsi14" in checked["available"]
        c["targets_with_weekly_ma20"] += "weekly_ma20" in checked["available"]
        c["targets_with_optional_weekly_ma120"] += "weekly_ma120" in checked["available"]
        c["targets_with_obv_proxy20_unverified"] += (
            "reported_acc_obv_proxy20_pct" in checked["available"])
        c["targets_blocked_at_latest_saved_day"] += (
            checked["status"] == "latest_saved_pair_missing_or_quarantined")
    return {
        "summary": {
            "schema": "v3-partial-price-research-0.1",
            "mode": "local_offline_private_research",
            "actual_api_calls": 0, "new_storage_writes": 0,
            "target_count": len(results), "archived_paired_market_days": archived_n,
            "historical_candidate_days_unverified": len(candidate_days),
            "last_archived_day": saved_dates[-1] if saved_dates else None,
            "last_archived_day_is_requested_end": (
                bool(saved_dates) and saved_dates[-1] == candidate_days[-1]),
            **{k: c[k] for k in (
                "targets_with_any_daily_indicator", "targets_with_daily_ma20",
                "targets_with_daily_ma60", "targets_with_rsi14",
                "targets_with_weekly_ma20", "targets_with_optional_weekly_ma120",
                "targets_with_obv_proxy20_unverified",
                "targets_blocked_at_latest_saved_day")},
            "weekly_120_ma_is_mandatory": False,
            "regular_session_volume_verified": False,
            "corporate_actions_verified": False,
            "historical_issuer_pit_verified": False,
            "full_v3_six_pillar_score_ready": False,
            "v3_release_ready": False,
            "confirmed_buy": False,
        },
        # Sensitive prices and names: NEVER log/export these.
        "private_results": results,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Local private offline partial technical research")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--private-db", required=True)
    p.add_argument("--calendar-source", choices=("weekdays", "xkrx"),
                   default="xkrx")
    p.add_argument("--market", choices=("KOSPI", "KOSDAQ"), required=True)
    p.add_argument("--code", required=True)
    a = p.parse_args(argv)
    try:
        output = run_optional_technical_research(
            start=a.start, end=a.end, private_db=a.private_db,
            targets=[{"market": a.market, "code": a.code}],
            calendar_source=a.calendar_source)
        print(json.dumps(output["summary"], sort_keys=True))
        return 0
    except PartialTechnicalError as err:
        print(json.dumps({
            "status": "blocked", "reason": str(err), "actual_api_calls": 0,
            "v3_release_ready": False, "confirmed_buy": False,
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
