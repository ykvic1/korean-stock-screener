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


def _outcome(case, horizon, cost_bps):
    bars = case["bars"]
    if len(bars) < horizon:
        return None  # Right-censored; never cherry-pick early winners.
    entry = bars[0]["open"]
    stop = case["stop_price"]
    target = case["target_price"]
    observed_low, observed_high = entry, entry
    exit_reason, exit_price, overlap = "horizon_close", None, False
    for candle in bars[:horizon]:
        op, hi, lo = (candle[k] for k in ("open", "high", "low"))
        observed_low = min(observed_low, lo)
        observed_high = max(observed_high, hi)
        # Opening gaps execute at the opening or worse; a target opening gap
        # receives only the planned target, conservatively.
        if op <= stop:
            exit_reason, exit_price = "stop_first", op
        elif op >= target:
            exit_reason, exit_price = "target_first", target
        elif lo <= stop and hi >= target:
            # OHLC cannot tell which happened first; assume STOP (conservative).
            exit_reason, exit_price, overlap = "stop_first", stop, True
        elif lo <= stop:
            exit_reason, exit_price = "stop_first", stop
        elif hi >= target:
            exit_reason, exit_price = "target_first", target
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
    }


def _p75(values):
    ordered = sorted(values)
    # Empirical nearest-rank upper quartile of downside magnitudes.
    return ordered[max(0, math.ceil(0.75 * len(ordered)) - 1)]


def _summary(outcomes, censored, excluded_by_embargo):
    n = len(outcomes)
    symbols = len({code for code, _, _ in outcomes})
    quarters = len({(d.year, (d.month - 1) // 3) for _, d, _ in outcomes})
    enough = n >= MIN_OBSERVATIONS and symbols >= MIN_SYMBOLS and quarters >= MIN_QUARTERS
    values = [out for _, _, out in outcomes]
    return {
        "n": n, "symbols": symbols, "signal_quarters": quarters,
        "right_censored": censored, "purged_or_embargoed": excluded_by_embargo,
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
        "ambiguous_same_bar_count": sum(
            v["same_bar_stop_target_ambiguous"] for v in values),
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
    validated = []
    for case in cases:
        signal = _check_case(case)
        key = (case["code"], signal)
        if key in seen:
            raise WalkForwardError("duplicate_symbol_signal")
        seen.add(key)
        validated.append((case, signal))
    result = {}
    for key, horizon in HORIZONS.items():
        cohorts = {"train": [], "holdout": []}
        censored = {"train": 0, "holdout": 0}
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
            outcome = _outcome(case, horizon, round_trip_cost_bps)
            if outcome is None:
                censored[group] += 1
                continue
            cohorts[group].append((case["code"], signal, outcome))
        result[key] = {
            "trading_sessions_proxy": horizon,
            "train": _summary(cohorts["train"], censored["train"], purged),
            "holdout": _summary(cohorts["holdout"], censored["holdout"], purged),
        }
    return {
        "schema": "v3-walkforward-exploratory-0.1",
        "split_date": cutoff.isoformat(),
        "embargo_calendar_days": embargo_days,
        "round_trip_cost_bps_assumption": round_trip_cost_bps,
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
                 "Survivorship bias, overlapping symbols, execution constraints "
                 "and provider contracts require independent verification."),
    }
