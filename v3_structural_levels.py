"""Causal observed daily and weekly structural support/resistance RESEARCH.

No Elliott wave probabilities, automatic stop fills, fitted targets or orders.
A level has to be confirmed with already observed right-side bars, and the
latest confirmed pivot is reported EVEN IF BROKEN (no hindsight relabel).
"""
from __future__ import annotations
import math
from v3_price_feature_adapter import _validated, PriceFeatureError

class StructureError(ValueError):
    """Fixed error code only."""


def _pivot(rows, side, left, right, lookback):
    if len(rows) < left + right + 1:
        return None
    idx = 0 if side == "support" else 1
    latest = None
    for i in range(max(left, len(rows)-lookback), len(rows)-right):
        value = rows[i][idx]
        prior = [r[idx] for r in rows[i-left:i]]
        later = [r[idx] for r in rows[i+1:i+right+1]]
        qualifies = (value < min(prior) and value <= min(later)) if idx==0 else (
            value > max(prior) and value >= max(later))
        if qualifies:
            latest = {"date":rows[i][2],"price":round(value,6),
                      "right_bars_already_observed":right}
    return latest


def structural_levels(price_bars, *, asof):
    if price_bars is None:
        return _result(asof, None, None, None, None, "no_price_history")
    try:
        df = _validated(price_bars, asof=asof)
    except (PriceFeatureError, KeyError, TypeError):
        raise StructureError("invalid_ohlcv_for_structure") from None
    day = [(float(v.Low),float(v.High),idx.strftime("%Y-%m-%d"))
           for idx,v in df.iterrows()]
    dlow = _pivot(day,"support",3,3,120)
    dhigh = _pivot(day,"resistance",3,3,120)
    wk = []
    weekkey = None
    for low,high,dateval in day:
        dt = __import__("datetime").date.fromisoformat(dateval)
        key = dt.isocalendar()[:2]
        if key != weekkey:
            wk.append([low,high,dateval])
            weekkey = key
        else:
            wk[-1][0] = min(wk[-1][0], low)
            wk[-1][1] = max(wk[-1][1], high)
            wk[-1][2] = dateval
    wlow = _pivot(wk,"support",2,2,52)
    whigh = _pivot(wk,"resistance",2,2,52)
    return _result(asof, dlow, dhigh, wlow, whigh, None,
                   last_close=float(df.Close.iloc[-1]), weeks=len(wk))


def _result(asof, dlow, dhigh, wlow, whigh, missing, *,
            last_close=None, weeks=0):
    def wrap(pivot, role):
        if pivot is None:
            return None
        val=pivot["price"]
        distance=100*(last_close/val-1)
        broken=last_close<val if role=="support" else last_close>val
        return {**pivot,"distance_close_vs_level_pct":round(distance,4),
                "level_invalidated_or_breached":broken,
                "confirmed_at_or_before_asof":True}
    d_s=wrap(dlow,"support");d_r=wrap(dhigh,"resistance")
    w_s=wrap(wlow,"support");w_r=wrap(whigh,"resistance")
    return {
        "schema":"v3-structural-levels-daily-weekly-0.1",
        "asof":asof,
        "daily_confirmed_support":d_s,
        "daily_confirmed_resistance":d_r,
        "weekly_confirmed_support":w_s,
        "weekly_confirmed_resistance":w_r,
        "observed_week_count":weeks,
        "reference_invalidation": (
            {"price": d_s["price"],
             "basis":"latest_confirmed_daily_pivot_low",
             "already_invalidated":d_s["level_invalidated_or_breached"]}
            if d_s else None
        ),
        "missing_reason":missing,
        "weekly_pattern_confirmed":False,
        "structural_price_is_trade_stop":False,
        "price_provenance_authenticated":False,
        "confirmed_buy":False,
    }
