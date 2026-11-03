"""Offline point-in-time walk-forward research. NEVER a live trading signal.

Only accepts pre-assembled, hypothetical or externally audited historical cases.
The input provenance flags and hashes are ASSERTIONS, not authentication. This
module neither obtains official data nor certifies real-world data completeness.
It never produces expected future returns, win probabilities or confirmed buys.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import math
import statistics

SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"
# Trading-session proxies, NOT calendar-month durations.
HORIZONS = {"1m": 21, "2m": 42, "3m": 63, "6m": 126, "12m": 252}
MIN_OBSERVATIONS = 40
MIN_SYMBOLS = 10
MIN_QUARTERS = 12
STOP_DISTANCE_BANDS = (
    "under_3", "3_to_under_6", "6_through_9", "over_9_to_under_12",
    "12_to_under_15", "15_to_under_25", "25_or_more",
)


class WalkForwardError(ValueError):
    """Error codes are static; never echo input prices, keys or raw feeds."""


def _date(value):
    if type(value) is not str:
        raise WalkForwardError("invalid_date")
    try:
        d = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise WalkForwardError("invalid_date") from None
    if d.isoformat() != value:
        raise WalkForwardError("invalid_date")
    return d


def _number(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and value > 0)


def _bar(obj, after):
    if not isinstance(obj, dict):
        raise WalkForwardError("invalid_bar")
    d = _date(obj.get("date"))
    if d <= after:
        raise WalkForwardError("unsorted_or_lookahead_bar")
    if obj.get("session") != SESSION:
        raise WalkForwardError("non_regular_session")
    if not all(_number(obj.get(k)) for k in ("open", "high", "low", "close")):
        raise WalkForwardError("nonpositive_or_nonfinite_ohlc")
    op, hi, lo, cl = (obj[k] for k in ("open", "high", "low", "close"))
    if lo > min(op, cl) or hi < max(op, cl) or lo > hi:
        raise WalkForwardError("inconsistent_ohlc")
    return d


def _check_case(case):
    if not isinstance(case, dict):
        raise WalkForwardError("invalid_case")
    code = case.get("code")
    if not (type(code) is str and len(code) == 6 and code.isascii()
            and code.isdigit()):
        raise WalkForwardError("invalid_symbol")
    # Issuer identity survives symbol renames / code transitions. Standalone
    # synthetic research without issuer provenance still falls back to code.
    issuer = case.get("issuer_id")
    if issuer is not None and not (
            type(issuer) is str and len(issuer) == 8
            and issuer.isascii() and issuer.isdigit()):
        raise WalkForwardError("invalid_issuer_identity")
    signal = _date(case.get("signal_date"))
    # These declarations only permit exploratory computation. They do NOT
    # prove provider provenance, the real PIT universe or adjustment history.
    evidence = case.get("evidence")
    required = ("pit_universe_asserted", "regular_session_asserted",
                "corporate_actions_audited_asserted",
                "trading_calendar_complete_asserted",
                "signal_setup_frozen_asserted")
    if not isinstance(evidence, dict) or any(evidence.get(x) is not True
                                               for x in required):
        raise WalkForwardError("missing_research_provenance_assertion")
    digest = evidence.get("signal_snapshot_sha256")
    if not (type(digest) is str and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)):
        raise WalkForwardError("missing_signal_snapshot_digest")
    stops = (case.get("stop_price"), case.get("target_price"))
    if not all(_number(x) for x in stops) or stops[0] >= stops[1]:
        raise WalkForwardError("invalid_frozen_levels")
    bars = case.get("bars")
    if not isinstance(bars, list) or not bars or len(bars) > 1500:
        raise WalkForwardError("invalid_history_length")
    prev = signal
    for item in bars:
        prev = _bar(item, prev)
    entry = bars[0]["open"]  # Strict next eligible session OPEN, not signal close.
    if not stops[0] < entry < stops[1]:
        raise WalkForwardError("gap_invalidates_entry_setup")
    if (_date(bars[0]["date"]) - signal).days > 14:
        raise WalkForwardError("late_entry_missing_calendar")
    return signal


def _stop_distance_band(distance):
    if (type(distance) not in (int,float) or not math.isfinite(distance)
            or not 0 < distance < 100):
        raise WalkForwardError("invalid_entry_stop_distance")
    if distance < 3:return STOP_DISTANCE_BANDS[0]
    if distance < 6:return STOP_DISTANCE_BANDS[1]
    if distance <= 9:return STOP_DISTANCE_BANDS[2]
    if distance < 12:return STOP_DISTANCE_BANDS[3]
    if distance < 15:return STOP_DISTANCE_BANDS[4]
    if distance < 25:return STOP_DISTANCE_BANDS[5]
    return STOP_DISTANCE_BANDS[6]


def _outcome(case, horizon, cost_bps):
    bars = case["bars"]
    if len(bars) < horizon:
        return None  # Right-censored; never cherry-pick early winners.
    entry = bars[0]["open"]
    stop = case["stop_price"]
    target = case["target_price"]
    observed_low, observed_high = entry, entry
    exit_reason, exit_price, overlap = "horizon_close", None, False
    stop_gap=False
    for candle in bars[:horizon]:
        op, hi, lo = (candle[k] for k in ("open", "high", "low"))
        # Only excursions that can occur BEFORE the modeled exit belong to
        # an executed trade. Entire exit-bar highs/lows may happen AFTER a
        # stop/target fill, and must never exaggerate historical MFE/MAE.
        if op <= stop:
            # An opening stop gap is a known immediate exit at the worse open.
            observed_low = min(observed_low, op)
            observed_high = max(observed_high, op)
            exit_reason, exit_price = "stop_first", op
            stop_gap=op < stop
        elif op >= target:
            # Conservative target fill at the planned limit, not the gap high.
            observed_low = min(observed_low, target)
            observed_high = max(observed_high, target)
            exit_reason, exit_price = "target_first", target
        elif lo <= stop:
            # OHLC has no intrabar event sequence. If both are touched,
            # assume the stop came first; no later high is credited to MFE.
            observed_low = min(observed_low, stop)
            observed_high = max(observed_high, op)
            exit_reason, exit_price = "stop_first", stop
            overlap = hi >= target
        elif hi >= target:
            # Worst-case low MAY precede the target; no later high above the
            # conservative target fill may inflate trade MFE.
            observed_low = min(observed_low, lo)
            observed_high = max(observed_high, target)
            exit_reason, exit_price = "target_first", target
        else:
            observed_low = min(observed_low, lo)
            observed_high = max(observed_high, hi)
        if exit_price is not None:
            break
    if exit_price is None:
        exit_price = bars[horizon - 1]["close"]
    return {
        "net_return_pct": (exit_price / entry - 1) * 100 - cost_bps / 100,
        "mfe_pct": (observed_high / entry - 1) * 100,
        "mae_pct": (observed_low / entry - 1) * 100,
        "event": exit_reason,
        "same_bar_stop_target_ambiguous": overlap,
        "stop_open_gap_below_frozen_level":stop_gap,
        "initial_stop_distance_pct_at_entry":(entry-stop)/entry*100,
    }


def _p75(values):
    ordered = sorted(values)
    # Empirical nearest-rank upper quartile of downside magnitudes.
    return ordered[max(0, math.ceil(0.75 * len(ordered)) - 1)]


def _summary(outcomes, censored, excluded_by_embargo, overlaps):
    n = len(outcomes)
    symbols = len({code for code, _, _ in outcomes})
    quarters = len({(d.year, (d.month - 1) // 3) for _, d, _ in outcomes})
    enough = n >= MIN_OBSERVATIONS and symbols >= MIN_SYMBOLS and quarters >= MIN_QUARTERS
    values = [out for _, _, out in outcomes]
    assumed_stop=sum(v["event"]=="stop_first" for v in values)
    target=sum(v["event"]=="target_first" for v in values)
    ambiguous=sum(v["same_bar_stop_target_ambiguous"] for v in values)
    def percent(count):
        return round(100*count/n,2) if enough else None
    return {
        "n": n, "symbols": symbols, "signal_quarters": quarters,
        "right_censored": censored, "purged_or_embargoed": excluded_by_embargo,
        "overlapping_same_symbol_suppressed": overlaps,
        "meets_exploratory_sample_floor": enough,
        "mean_historical_net_pct": round(statistics.mean(
            v["net_return_pct"] for v in values), 4) if enough else None,
        "median_historical_net_pct": round(statistics.median(
            v["net_return_pct"] for v in values), 4) if enough else None,
        "historical_stop_first_pct": round(100 * sum(
            v["event"] == "stop_first" for v in values) / n, 2) if enough else None,
        "historical_target_first_pct": round(100 * sum(
            v["event"] == "target_first" for v in values) / n, 2) if enough else None,
        "historical_adverse_p75_pct": round(_p75([
            max(0, -v["mae_pct"]) for v in values]), 4) if enough else None,
        "historical_favorable_mean_pct": round(statistics.mean(
            v["mfe_pct"] for v in values), 4) if enough else None,
        "historical_mean_net_initial_risk_units":round(statistics.mean(
            v["net_return_pct"]/v["initial_stop_distance_pct_at_entry"]
            for v in values),4) if enough else None,
        "historical_adverse_p75_initial_risk_units":round(_p75([
            max(0,-v["mae_pct"])/v["initial_stop_distance_pct_at_entry"]
            for v in values]),4) if enough else None,
        "ambiguous_same_bar_count":ambiguous,
        "unambiguous_stop_first_count":assumed_stop-ambiguous,
        "unambiguous_target_first_count":target,
        "stop_open_gap_below_frozen_level_count":sum(
            v["stop_open_gap_below_frozen_level"] for v in values),
        "intrabar_order_stop_first_bounds_pct":{
            "lower":percent(assumed_stop-ambiguous),"upper":percent(assumed_stop)},
        "intrabar_order_target_first_bounds_pct":{
            "lower":percent(target),"upper":percent(target+ambiguous)},
        "intrabar_bounds_are_confidence_intervals":False,
        "interpretation": "exploratory_retrospective" if enough else "insufficient_sample",
        "forward_expected_return_pct": None,
        "forecast_success_probability": None,
        "confirmed_buy": False,
    }


def evaluate(cases, *, split_date, embargo_days=252, round_trip_cost_bps=30):
    """Independent time cohorts with future-label purging and no training leakage.

    Training cases are admitted only when the WHOLE horizon has ended before
    split_date. Test cases must have signals at least embargo_days AFTER split.
    Cases in-between are purged. No parameter optimization occurs here.
    """
    cutoff = _date(split_date)
    if (type(embargo_days) is not int or not 0 <= embargo_days <= 730
            or type(round_trip_cost_bps) not in (int, float)
            or not math.isfinite(round_trip_cost_bps)
            or not 0 <= round_trip_cost_bps <= 500):
        raise WalkForwardError("invalid_research_configuration")
    if not isinstance(cases, list) or len(cases) > 100000:
        raise WalkForwardError("invalid_research_cohort")
    seen = set()
    seen_codes = set()
    validated = []
    for case in cases:
        signal = _check_case(case)
        identity = case.get("issuer_id") or case["code"]
        key = (identity, signal)
        code_key = (case["code"], signal)
        if key in seen or code_key in seen_codes:
            raise WalkForwardError("duplicate_symbol_signal")
        seen.add(key)
        seen_codes.add(code_key)
        validated.append((case, signal))
    # Input order must not change the outcome or which issuer case wins.
    validated.sort(key=lambda item: (
        item[1], item[0].get("issuer_id") or item[0]["code"],
        item[0]["code"]))
    result = {}
    for key, horizon in HORIZONS.items():
        cohorts = {"train": [], "holdout": []}
        censored = {"train": 0, "holdout": 0}
        overlaps = {"train": 0, "holdout": 0}
        # Use one signal per symbol per entire PLANNED holding window,
        # regardless of early exit, to avoid dependent duplicated labels.
        previous_window_end = {"train": {}, "holdout": {}}
        purged = 0
        for case, signal in validated:
            bars = case["bars"]
            # Purge even early stop/target hits unless the planned full
            # observation horizon is available BEFORE the split boundary.
            if signal < cutoff:
                if len(bars) < horizon:
                    censored["train"] += 1
                    continue
                if _date(bars[horizon - 1]["date"]) >= cutoff:
                    purged += 1
                    continue
                group = "train"
            elif signal >= cutoff + timedelta(days=embargo_days):
                group = "holdout"
                if len(bars) < horizon:
                    censored[group] += 1
                    continue
            else:
                purged += 1
                continue
            identity = case.get("issuer_id") or case["code"]
            prior_end = previous_window_end[group].get(identity)
            if prior_end is not None and signal <= prior_end:
                overlaps[group] += 1
                continue
            outcome = _outcome(case, horizon, round_trip_cost_bps)
            if outcome is None:
                censored[group] += 1
                continue
            previous_window_end[group][identity] = _date(
                bars[horizon - 1]["date"])
            cohorts[group].append((identity, signal, outcome))
        distance_groups={band:{"train":[],"holdout":[]} for band in STOP_DISTANCE_BANDS}
        for group in ("train","holdout"):
            for item in cohorts[group]:
                band=_stop_distance_band(item[2]["initial_stop_distance_pct_at_entry"])
                distance_groups[band][group].append(item)
        result[key] = {
            "trading_sessions_proxy": horizon,
            "train": _summary(cohorts["train"], censored["train"], purged,
                              overlaps["train"]),
            "holdout": _summary(cohorts["holdout"], censored["holdout"], purged,
                                overlaps["holdout"]),
            # Partition only AFTER global same-issuer overlap suppression,
            # purging and full-horizon censoring. Moving a signal to another
            # distance band must never make a duplicate label independent.
            "stop_distance_research":{
                "basis":"actual_next_session_entry_open_to_frozen_stop",
                "cuts_fixed_without_optimization":True,
                "bands":{
                    band:{group:_summary(distance_groups[band][group],0,0,0)
                          for group in ("train","holdout")}
                    for band in STOP_DISTANCE_BANDS},
                "exclusions_counted_in_parent_cohort_only":True,
                "usable_for_live_risk_points":False,
            },
        }
    return {
        "schema": "v3-walkforward-exploratory-0.1",
        "split_date": cutoff.isoformat(),
        "embargo_calendar_days": embargo_days,
        "round_trip_cost_bps_assumption": round_trip_cost_bps,
        "mfe_mae_exit_bar_policy": "conservative_pre_exit_ohlc_only",
        "same_bar_event_policy":"stop_first_assumption_with_order_bounds",
        "execution_depth_and_stop_fill_slippage_verified":False,
        "input_unique_signal_count": len(validated),
        "periods": result,
        "provider_provenance_authenticated": False,
        "pit_universe_independently_verified": False,
        "full_calendar_independently_verified": False,
        "out_of_sample_strategy_validated": False,
        "survivorship_bias_resolved": False,
        "future_return_forecasts": None,
        "buy_signals": None,
        "authorized_for_v3_scoring": False,
        "note": ("Research input assertions are not authenticated evidence. "
                 "Survivorship bias, issuer aliases, execution constraints "
                 "and provider contracts require independent verification."),
    }
