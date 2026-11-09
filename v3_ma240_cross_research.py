"""Dated 20/60-day bullish moving-average crossing near the 240-day MA.

A cross qualifies only when the *intersection of the two moving averages*,
NOT the closing share price, lies within +/-12% of the interpolated 240-day
moving average at that intersection. Lookback is 20 completed sessions.
This is provisional FDR price research, not verified adjusted KRX history.
"""
from __future__ import annotations

from datetime import date
import math

SCHEMA = "v3-ma20-ma60-cross-near-ma240-20session-shadow-0.1"
RECENT_SESSIONS = 20
MIN_HISTORY_SESSIONS = 260
MAX_POINTS = 6
BAND_PCT = 12.0
UNAVAILABLE_REASONS = frozenset({
    "insufficient_260_sessions",
    "missing_or_mismatched_session_index",
    "irregular_or_stale_session_calendar",
    "nonpositive_or_invalid_close",
    "nonpositive_or_invalid_volume",
    "possible_unadjusted_corporate_action",
    "other_ma240_validation_failure",
})


def _date(raw):
    if type(raw) is date:
        return raw
    if callable(getattr(raw, "date", None)):
        result = raw.date()
        if type(result) is date:
            return result
    return None


def _iso(value):
    if type(value) is not str:
        return None
    try:
        d = date.fromisoformat(value)
        return d if d.isoformat() == value else None
    except ValueError:
        return None


def _number(value, *, positive=True):
    # NumPy numeric scalars are acceptable, but bools and non-finite values
    # cannot masquerade as daily closes or volumes.
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) and (result > 0 if positive else result >= 0) else None


def valid_unavailable_reason(observation, reason):
    """A missing 240-day observation needs a finite, auditable source reason.

    Reasons are independent of the 0/6 investment event. A null observation
    must never be promoted to a zero-point observed event.
    """
    if observation is not None:
        return reason is None
    return type(reason) is str and reason in UNAVAILABLE_REASONS


def classify_unavailable(close, volume, *, asof, session_days):
    """Classify *in-memory* rejected history without further provider calls.

    Only call when ``observe`` returned None. This never changes its strict
    260-session lookback, cross calculation, or investment availability.
    """
    if len(close) < MIN_HISTORY_SESSIONS:
        return "insufficient_260_sessions"
    if (session_days is None or len(close) != len(volume) or
            len(close) != len(session_days) or _iso(asof) is None):
        return "missing_or_mismatched_session_index"
    dates = [_date(value) for value in session_days[-MIN_HISTORY_SESSIONS:]]
    if (any(d is None for d in dates) or dates[-1] != _iso(asof) or
            any(not 0 < (b-a).days <= 10 for a, b in zip(dates, dates[1:]))):
        return "irregular_or_stale_session_calendar"
    closes = [_number(v) for v in close[-MIN_HISTORY_SESSIONS:]]
    if any(v is None for v in closes):
        return "nonpositive_or_invalid_close"
    volumes = [_number(v, positive=False) for v in volume[-MIN_HISTORY_SESSIONS:]]
    if any(v is None for v in volumes) or volumes[-1] <= 0:
        return "nonpositive_or_invalid_volume"
    if any(abs(closes[i] / closes[i-1] - 1) > 0.5
           for i in range(1, len(closes))):
        return "possible_unadjusted_corporate_action"
    return "other_ma240_validation_failure"


def observe(close, volume, *, asof, session_days):
    """Use exactly the last 260 observed sessions; return None if untrustworthy.

    Return a dated 0/6 event or an unavailable observation, never a guessed
    6/6 for a stock with fewer than 260 bars or suspicious corporate actions.
    """
    day = _iso(asof)
    if (day is None or session_days is None or
            len(close) != len(volume) or len(close) != len(session_days) or
            len(close) < MIN_HISTORY_SESSIONS):
        return None
    dates = [_date(value) for value in session_days[-MIN_HISTORY_SESSIONS:]]
    closes = [_number(v) for v in close[-MIN_HISTORY_SESSIONS:]]
    volumes = [_number(v, positive=False) for v in volume[-MIN_HISTORY_SESSIONS:]]
    if (any(d is None for d in dates) or dates[-1] != day or
            any(not 0 < (b-a).days <= 10 for a, b in zip(dates, dates[1:])) or
            any(v is None for v in closes) or
            any(v is None for v in volumes) or volumes[-1] <= 0):
        return None
    # Moving-average intersections use reported closes, not trading volume.
    # A historical zero-volume day does not erase its positive reported
    # closing price. Do not fill prices, remove sessions or claim trading
    # during that day. Latest volume must still be positive; negative or
    # non-finite historical volumes, missing closes and split-like jumps
    # remain rejected independently.
    # An unadjusted large split could manufacture a false MA cross.
    if any(abs(closes[i] / closes[i-1] - 1) > 0.5
           for i in range(1, len(closes))):
        return None

    # Prefix sums permit precise 20/60/240-session means on each event day.
    sums = [0.0]
    for price in closes:
        sums.append(sums[-1] + price)
    def ma(period, end):
        return (sums[end+1]-sums[end+1-period]) / period

    crosses = []
    for i in range(MIN_HISTORY_SESSIONS-RECENT_SESSIONS,
                   MIN_HISTORY_SESSIONS):
        p20, p60, p240 = ma(20,i-1), ma(60,i-1), ma(240,i-1)
        c20, c60, c240 = ma(20,i), ma(60,i), ma(240,i)
        previous_gap = p20-p60
        current_gap = c20-c60
        if not (previous_gap <= 0 < current_gap):
            continue
        # Intersection, not current close and not one MA alone.
        denominator = current_gap-previous_gap
        fraction = -previous_gap/denominator
        cross_level = p20 + fraction*(c20-p20)
        ma240_at_cross = p240 + fraction*(c240-p240)
        if (not math.isfinite(cross_level) or
                not math.isfinite(ma240_at_cross) or ma240_at_cross <= 0):
            return None
        distance = 100*(cross_level/ma240_at_cross-1)
        if not math.isfinite(distance):
            return None
        crosses.append((dates[i].isoformat(), distance))

    qualifying = [entry for entry in crosses if abs(entry[1]) <= BAND_PCT]
    last = crosses[-1] if crosses else None
    matched = qualifying[-1] if qualifying else None
    return {
        "schema": SCHEMA, "asof": asof,
        "recent_sessions": RECENT_SESSIONS,
        "history_sessions_used": MIN_HISTORY_SESSIONS,
        "cross_event_count": len(crosses),
        "last_cross_date": last[0] if last else None,
        "last_cross_point_vs_ma240_pct": round(last[1], 6) if last else None,
        "qualified": matched is not None,
        "qualifying_cross_date": matched[0] if matched else None,
        "qualifying_cross_point_vs_ma240_pct": round(matched[1], 6) if matched else None,
        "reference_band_pct": BAND_PCT,
        "cross_point_based_on_price_ma_intersection": True,
        "closing_price_band_test_used": False,
        "source_regular_session_verified": False,
        "source_corporate_action_adjusted_verified": False,
        "certified_investment_points": None,
    }


def valid_observation(item, asof):
    """Validate the bounded public derived receipt; no certification claims."""
    if (type(item) is not dict or item.get("schema") != SCHEMA or
            item.get("asof") != asof or _iso(asof) is None or
            item.get("recent_sessions") != RECENT_SESSIONS or
            item.get("history_sessions_used") != MIN_HISTORY_SESSIONS or
            item.get("reference_band_pct") != BAND_PCT or
            item.get("cross_point_based_on_price_ma_intersection") is not True or
            item.get("closing_price_band_test_used") is not False or
            item.get("source_regular_session_verified") is not False or
            item.get("source_corporate_action_adjusted_verified") is not False or
            item.get("certified_investment_points") is not None or
            type(item.get("qualified")) is not bool or
            type(item.get("cross_event_count")) is not int or
            not 0 <= item["cross_event_count"] <= RECENT_SESSIONS):
        return False
    n = item["cross_event_count"]
    last_day, last_pct = (item.get("last_cross_date"),
                          item.get("last_cross_point_vs_ma240_pct"))
    if n == 0:
        if last_day is not None or last_pct is not None:
            return False
    elif (_iso(last_day) is None or
          not type(last_pct) in (int, float) or
          not math.isfinite(last_pct) or
          not 0 <= (_iso(asof)-_iso(last_day)).days <= 35):
        return False
    qualify_day = item.get("qualifying_cross_date")
    qualify_pct = item.get("qualifying_cross_point_vs_ma240_pct")
    if item["qualified"]:
        return (n > 0 and _iso(qualify_day) is not None and
                type(qualify_pct) in (int, float) and
                math.isfinite(qualify_pct) and abs(qualify_pct) <= BAND_PCT and
                0 <= (_iso(asof)-_iso(qualify_day)).days <= 35 and
                qualify_day <= last_day)
    return qualify_day is None and qualify_pct is None
