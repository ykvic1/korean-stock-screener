"""v3 OFFLINE loss-first research SCREEN, ahead of high-score presentation.

This examines past, independently unverified OHLCV, raw factor observations
and preexisting finance/industry warning candidates. It NEVER certifies
a market observation, supplies an investment recommendation, sets an order,
derives an expected return or overrides the v3 release lock.

The pivot low is confirmed by three ALREADY OBSERVED following sessions:
no future-bar lookahead at the signal date. No fixed-percentage stop is
substituted for a missing structural support.
"""
from __future__ import annotations

import math
from v3_price_feature_adapter import _validated, PriceFeatureError

FINANCE_WARNINGS = frozenset({
    "negative_or_zero_equity",
    "negative_owners_profit",
    "negative_latest_free_cash_flow",
    "net_debt_above_equity",
})
INDUSTRY_WARNINGS = frozenset({
    "zero_or_loss_base_prevents_growth_ratio",
    "aggregate_industry_operating_profit_declining",
    "low_declared_cohort_above_ma_breadth",
})
MAX_HISTORY = 4000
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
PIVOT_LOOKBACK = 120


class LossScreenError(ValueError):
    """Only predefined reason codes, not source values."""


def _bounded(value, *, floor=None, ceil=None):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise LossScreenError("invalid_loss_screen_numeric_input")
    if floor is not None and value < floor or ceil is not None and value > ceil:
        raise LossScreenError("invalid_loss_screen_numeric_domain")
    return float(value)


def _warning_set(value, allowed):
    if value is None:
        return set()
    if not isinstance(value, (tuple, list, set)) or (
            any(type(flag) is not str or flag not in allowed for flag in value)):
        raise LossScreenError("unrecognized_risk_warning")
    return set(value)


def _last_confirmed_pivot_low(df):
    """3-left / 3-right local low with no right-side observations from future.

    Latest confirmed pivot is most relevant structural reference, including
    if subsequently broken. Never fall back to a conveniently older pivot
    to conceal a broken latest level.
    """
    if len(df) < PIVOT_LEFT + PIVOT_RIGHT + 1:
        return None
    lows = df["Low"].to_numpy(dtype=float)
    last = None
    start = max(PIVOT_LEFT, len(lows) - PIVOT_LOOKBACK)
    for i in range(start, len(lows) - PIVOT_RIGHT):
        if (lows[i] < min(lows[i - PIVOT_LEFT:i])
                and lows[i] <= min(lows[i + 1:i + 1 + PIVOT_RIGHT])):
            last = (df.index[i].date().isoformat(), float(lows[i]))
    return last


def loss_first_screen(*, asof, price_bars=None, raw_features=None,
                      financial_warning_candidates=None,
                      industry_warning_candidates=None):
    """Classify *research review priority*, never a real-world buy decision.

    statuses:
      structural_exclusion: confirmed support broken or equity <= 0.
      downside_watch: negative observed trend / investor net selling or
                      serious financial / industry warning.
      chase_risk: predeclared price-extension heuristics, not a forecast.
      incomplete_evidence: insufficient structure/data to judge safely.
      conditional_research_review: technical & flow evidence positive,
                       anchored local-low stop available; STILL NOT A BUY.
      watch_unconfirmed: mixed signals with no proof of reversal.
    """
    if type(asof) is not str:
        raise LossScreenError("invalid_loss_screen_asof")
    # Even without price observations ensure dates have exact ISO format.
    from datetime import date
    try:
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError()
    except ValueError:
        raise LossScreenError("invalid_loss_screen_asof") from None
    if raw_features is None:
        raw_features = {}
    if not isinstance(raw_features, dict):
        raise LossScreenError("invalid_loss_screen_features")
    required_metrics = (
        "ias_flow20_pct_float", "ias_flow60_pct_float",
        "ias_obv_change_60_pct",
    )
    metric = {name: _bounded(raw_features.get(name)) for name in required_metrics}
    finance = _warning_set(financial_warning_candidates, FINANCE_WARNINGS)
    industry = _warning_set(industry_warning_candidates, INDUSTRY_WARNINGS)

    df = None
    if price_bars is not None:
        try:
            df = _validated(price_bars, asof=asof)
        except (PriceFeatureError, TypeError, KeyError):
            raise LossScreenError("invalid_research_price_evidence") from None

    risk = []
    watch = []
    gap_to_support = None
    reference = None
    daily_trend = "unavailable"
    price_20_pct = None
    price_60_pct = None
    volume_ratio = None
    confirmed_stop = False
    if "negative_or_zero_equity" in finance:
        risk.append("nonpositive_equity_exclusion")
    if "negative_owners_profit" in finance:
        watch.append("negative_owner_earnings")
    if "negative_latest_free_cash_flow" in finance:
        watch.append("negative_last_fcf")
    if "net_debt_above_equity" in finance:
        watch.append("net_debt_exceeds_equity")
    if "aggregate_industry_operating_profit_declining" in industry:
        watch.append("industry_profit_declining")
    if "zero_or_loss_base_prevents_growth_ratio" in industry:
        watch.append("industry_turnaround_growth_unquantifiable")
    if "low_declared_cohort_above_ma_breadth" in industry:
        watch.append("weak_industry_market_breadth")

    if df is not None and len(df) >= 61:
        close = df["Close"].to_numpy(dtype=float)
        vol = df["Volume"].to_numpy(dtype=float)
        ma20, ma60 = float(close[-20:].mean()), float(close[-60:].mean())
        last_close = float(close[-1])
        price_20_pct = round((last_close / close[-21] - 1) * 100, 6)
        price_60_pct = round((last_close / close[-61] - 1) * 100, 6)
        daily_trend = (
            "downtrend" if last_close < ma60 and ma20 < ma60 else
            "uptrend" if last_close > ma20 and ma20 >= ma60 else
            "transition_or_range"
        )
        pivot = _last_confirmed_pivot_low(df)
        if pivot is not None:
            pivot_day, price = pivot
            reference = {
                "observed_confirmed_swing_low_day": pivot_day,
                "observed_confirmed_swing_low_price": round(price, 6),
                "structural_reference_is_trade_stop_order": False,
            }
            gap_to_support = round(100 * (1 - price / last_close), 6)
            if last_close < price:
                risk.append("latest_confirmed_swing_low_broken")
            elif gap_to_support <= 0:
                watch.append("structural_stop_unusable_or_at_market")
            else:
                confirmed_stop = True
        else:
            watch.append("no_confirmed_recent_swing_low")
        if daily_trend == "downtrend":
            watch.append("price_below_declining_short_medium_structure")
        if price_20_pct > 30 or price_60_pct > 50:
            watch.append("extended_price_chase_risk")
        if len(vol) >= 21:
            avg = float(vol[-21:-1].mean())
            if avg > 0:
                volume_ratio = round(float(vol[-1] / avg), 6)
                if volume_ratio > 5:
                    watch.append("exceptional_volume_requires_manual_review")
    else:
        watch.append("insufficient_61_session_price_history")

    if metric["ias_flow20_pct_float"] is not None and metric["ias_flow60_pct_float"] is not None:
        if metric["ias_flow20_pct_float"] < 0 and metric["ias_flow60_pct_float"] < 0:
            watch.append("persistent_observed_net_selling_20_and_60")
    else:
        watch.append("investor_20_60_evidence_missing")
    if metric["ias_obv_change_60_pct"] is not None:
        if metric["ias_obv_change_60_pct"] < 0:
            watch.append("negative_price_volume_obv_60")
    else:
        watch.append("obv_volume_direction_missing")

    if risk:
        status = "structural_exclusion"
    elif any(k in watch for k in (
            "negative_owner_earnings", "negative_last_fcf",
            "price_below_declining_short_medium_structure",
            "persistent_observed_net_selling_20_and_60",
            "industry_profit_declining")):
        status = "downside_watch"
    elif "extended_price_chase_risk" in watch:
        status = "chase_risk"
    elif df is None or len(df) < 61 or not confirmed_stop:
        status = "incomplete_evidence"
    elif (daily_trend == "uptrend"
          and metric["ias_flow20_pct_float"] is not None
          and metric["ias_flow60_pct_float"] is not None
          and metric["ias_flow20_pct_float"] > 0
          and metric["ias_flow60_pct_float"] > 0
          and metric["ias_obv_change_60_pct"] is not None
          and metric["ias_obv_change_60_pct"] > 0
          and not watch):
        status = "conditional_research_review"
    else:
        status = "watch_unconfirmed"

    return {
        "schema": "v3-loss-first-research-gate-0.1",
        "asof": asof,
        "status_research_only": status,
        "exclusion_reasons": sorted(set(risk)),
        "review_reasons": sorted(set(watch)),
        "daily_trend_observation": daily_trend,
        "observed_20_session_return_pct": price_20_pct,
        "observed_60_session_return_pct": price_60_pct,
        "today_to_previous_20_volume_ratio": volume_ratio,
        "latest_confirmed_swing_low": reference,
        "distance_to_observed_swing_low_pct": gap_to_support,
        "structural_exit_reference_available": confirmed_stop,
        "risk_gate_overrides_provisional_high_scores": bool(risk),
        "safe_position_size": None,
        "execution_or_gap_loss_guaranteed": False,
        "weekly_structure_independently_confirmed": False,
        "authenticated_market_evidence": False,
        "probabilities_empirically_calibrated": False,
        "estimated_expected_return": None,
        "confirmed_buy": False,
        "v3_release_ready": False,
    }
