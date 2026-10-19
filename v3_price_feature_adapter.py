"""Offline raw OHLCV -> v3 technical/momentum/IAS-obv candidate features.

Causal trailing calculations, including partial current trading week. No
official provider, adjustment history, corporate-action calendar, issuer
verification, RSI pattern success rate or real-world buy recommendation.
"""
from __future__ import annotations

from datetime import date
import math
import numpy as np
import pandas as pd

from technical_v22 import rsi, divergence, pattern_snapshot

# Safety bound, NOT an investment lookback. About two decades of daily
# observations may be accepted; research indicators keep their own windows.
MAX_BARS = 6000
SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"


class PriceFeatureError(ValueError):
    """Fixed reason names, no raw source prices or private values in errors."""


def _validated(bars, *, asof):
    if not isinstance(bars, list) or not bars or len(bars) > MAX_BARS:
        raise PriceFeatureError("invalid_ohlcv_history")
    if type(asof) is not str:
        raise PriceFeatureError("invalid_ohlcv_asof")
    try:
        expected = date.fromisoformat(asof)
    except ValueError:
        raise PriceFeatureError("invalid_ohlcv_asof") from None
    prev = None
    rows = []
    for bar in bars:
        if not isinstance(bar, dict):
            raise PriceFeatureError("invalid_ohlcv_row")
        try:
            d = date.fromisoformat(bar.get("date"))
        except (TypeError, ValueError):
            raise PriceFeatureError("invalid_ohlcv_date") from None
        if prev is not None and d <= prev or d > expected:
            raise PriceFeatureError("future_duplicate_or_unsorted_ohlcv")
        prev = d
        if bar.get("session") != SESSION:
            raise PriceFeatureError("unknown_regular_session_provenance")
        values = []
        for key in ("open", "high", "low", "close"):
            value = bar.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise PriceFeatureError("invalid_ohlc")
            values.append(float(value))
        op, hi, lo, cl = values
        if lo > min(op, cl) or hi < max(op, cl) or lo > hi:
            raise PriceFeatureError("ohlc_range_inconsistent")
        vol = bar.get("volume")
        if type(vol) is not int or vol < 0:
            raise PriceFeatureError("invalid_daily_share_volume")
        rows.append((d, op, hi, lo, cl, vol))
    if prev != expected:
        raise PriceFeatureError("stale_last_price_bar")
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    return df.set_index(pd.DatetimeIndex(df.pop("Date")))


def _benchmark(values, *, asof):
    if values is None:
        return None
    if not isinstance(values, list) or not values or len(values) > MAX_BARS:
        raise PriceFeatureError("invalid_benchmark")
    previous, observations = None, {}
    for row in values:
        if not isinstance(row, dict):
            raise PriceFeatureError("invalid_benchmark_row")
        try:
            d = date.fromisoformat(row.get("date"))
        except (TypeError, ValueError):
            raise PriceFeatureError("invalid_benchmark_date") from None
        if previous is not None and d <= previous or d > date.fromisoformat(asof):
            raise PriceFeatureError("future_duplicate_or_unsorted_benchmark")
        v = row.get("close")
        if type(v) not in (float, int) or not math.isfinite(v) or v <= 0:
            raise PriceFeatureError("invalid_benchmark_close")
        observations[d.isoformat()] = float(v)
        previous = d
    return observations


def _relative(close, benchmark, dates, back):
    if benchmark is None or len(close) <= back:
        return None
    # No forward or nearest-neighbor substitution for a missing index date.
    sample_dates = dates[-(back + 1):]
    if any(d not in benchmark for d in sample_dates):
        return None
    market_return = benchmark[sample_dates[-1]] / benchmark[sample_dates[0]]
    return round(float(100 * ((close[-1] / close[-back-1]) / market_return - 1)), 6)


def extract_price_features(bars, *, asof, market_index=None,
                           industry_index=None):
    """Derive a subset of v3_research_factor_engine feature columns.

    The 120-week indicator uses 119 completed ISO-week closes plus the
    current partial week last close, as in the existing v2.2 engine.
    Units: OBV_delta / sum(volume_last60) * 100, NOT percent change on
    arbitrary, zero-crossing OBV base.
    """
    df = _validated(bars, asof=asof)
    market = _benchmark(market_index, asof=asof)
    industry = _benchmark(industry_index, asof=asof)
    c = df.Close.to_numpy(dtype=float)
    vol = df.Volume.to_numpy(dtype=float)
    n = len(df)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    features = {
        "ias_obv_change_60_pct": None,
        "technical_weekly_ma120_reclaim_strength": None,
        "technical_weekly_rsi_divergence_strength": None,
        "technical_pattern_confirmation_strength": None,
        "technical_volume_breakout_ratio": None,
        "momentum_stock_relative_strength_20_pct": None,
        "momentum_stock_relative_strength_60_pct": None,
        "momentum_positive_days_20_ratio": None,
        "industry_relative_strength_60_pct": None,
    }
    # Positive OBV within lower price changes can be an interesting *candidate*
    # but requires verified regular-only session volumes and corporate actions.
    if n >= 61 and vol[-60:].sum() > 0:
        obv_moves = np.sign(c[-60:] - c[-61:-1])
        features["ias_obv_change_60_pct"] = round(
            100 * float(np.dot(obv_moves, vol[-60:])) / float(vol[-60:].sum()), 6)
    if n >= 21:
        base = float(vol[-21:-1].mean())
        if base > 0:
            features["technical_volume_breakout_ratio"] = round(float(vol[-1] / base), 6)
        features["momentum_positive_days_20_ratio"] = round(float(np.mean(
            c[-20:] > c[-21:-1])), 6)
    if n >= 61:
        features["momentum_stock_relative_strength_20_pct"] = (
            _relative(c, market, dates, 20))
        features["momentum_stock_relative_strength_60_pct"] = (
            _relative(c, market, dates, 60))
        if market is not None and industry is not None:
            matched = dates[-61:]
            if all(day in market and day in industry for day in matched):
                features["industry_relative_strength_60_pct"] = round(
                    100 * ((industry[matched[-1]] / industry[matched[0]]) /
                           (market[matched[-1]] / market[matched[0]]) - 1), 6)
    if n >= 90:
        patterns = pattern_snapshot(df)
        if patterns["status"] == "heuristic_unvalidated":
            # Fixed geometric priority, NOT a statistical win probability.
            candidates = {
                "inverse_head_shoulders_breakout_candidate": 1.0,
                "falling_wedge_candidate": 0.75,
                "triangle_candidate": 0.65,
                "post_rise_consolidation_candidate": 0.70,
            }
            features["technical_pattern_confirmation_strength"] = max(
                (value for name, value in candidates.items()
                 if patterns.get(name) is True), default=0.0)
    # Use one observed partial last week (current session) only, never a
    # future Friday's close from a fully hindsight-resampled weekly candle.
    if n >= 120:
        weeks = []
        last_week = None
        for i, d in enumerate(df.index):
            key = d.isocalendar()[:2]
            if key != last_week:
                weeks.append(float(c[i]))
                last_week = key
            else:
                weeks[-1] = float(c[i])
        if len(weeks) >= 128:
            w = np.asarray(weeks)
            averages = np.array([
                np.mean(w[i-119:i+1]) for i in range(119, len(w))
            ])
            gap = 100 * (w[-1] / averages[-1] - 1)
            last_eight = w[-8:]
            last_eight_ma = averages[-8:]
            previously_below = bool(np.any(last_eight[:-1] <= last_eight_ma[:-1]))
            if 0 <= gap <= 12 and previously_below:
                features["technical_weekly_ma120_reclaim_strength"] = round(
                    float(0.6 + 0.4 * (1 - gap/12)), 6)
            else:
                # Not re-claiming from below; a mere far-above 120-week line
                # must not receive an early-reversal bonus.
                features["technical_weekly_ma120_reclaim_strength"] = 0.0
            weekly_rsi = rsi(w, period=14)
            bull, _ = divergence(w, weekly_rsi, span=2, max_age=30)
            features["technical_weekly_rsi_divergence_strength"] = (
                1.0 if bool(np.any(bull[-8:])) else 0.0)
    r20 = 100 * (c[-1] / c[-21] - 1) if n >= 21 else None
    r60 = 100 * (c[-1] / c[-61] - 1) if n >= 61 else None
    # Do not assign a buy/sell; a price rise without controls can be chase risk.
    risk_flags = []
    if r20 is not None and r20 > 30:
        risk_flags.append("four_week_price_extension_review")
    if r60 is not None and r60 > 50:
        risk_flags.append("twelve_week_price_extension_review")
    if features["technical_volume_breakout_ratio"] is not None and (
            features["technical_volume_breakout_ratio"] > 5):
        risk_flags.append("exceptional_single_day_volume_review")
    return {
        "schema": "v3-technical-price-input-adapter-0.1",
        "asof": asof,
        "observed_rows": n,
        "partial_week_included": True,
        "features": features,
        "available_features": [k for k, v in features.items() if v is not None],
        "missing_features": [k for k, v in features.items() if v is None],
        "price_extension_caution": risk_flags,
        "pattern_success_probability": None,
        "source_regular_only_volume_verified": False,
        "corporate_action_history_verified": False,
        "historical_universe_authenticated": False,
        "safe_to_feed_investable_v3_score": False,
        "confirmed_buy": False,
    }
