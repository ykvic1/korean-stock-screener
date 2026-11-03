"""Research-only multiple-horizon ACTUAL trading-value observations.

Accept only an explicit historical 'Amount' field (KRW) paired with closing
prices. Never substitute Close*Volume, one-day Amount, shares, or a filled
missing horizon. No V3 score or certified regular-session claim is emitted.
"""
from __future__ import annotations

from datetime import date
import math

SCHEMA = "v3-actual-trading-value-multihorizon-shadow-0.1"
DAILY_HORIZONS = (20, 60, 120, 240)
WEEKLY_HORIZONS = (5, 10, 20, 60, 120)
MAX_ROWS = 2200


class TurnoverHistoryError(ValueError):
    """Known fixed errors: never embed private source prices in messages."""


def _day(value):
    if type(value) is not str:
        raise TurnoverHistoryError("invalid_observed_date")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise TurnoverHistoryError("invalid_observed_date") from None
    if result.isoformat() != value:
        raise TurnoverHistoryError("invalid_observed_date")
    return result


def _number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise TurnoverHistoryError("missing_actual_trading_value_or_price")
    if (value <= 0 if positive else value < 0):
        raise TurnoverHistoryError("invalid_observed_trading_value_or_price")
    return float(value)


def _windows(observations, horizons, value_key):
    answer = {}
    for period in horizons:
        if len(observations) < period:
            answer[str(period)] = None
            continue
        subset = observations[-period:]
        values = [r[value_key] for r in subset]
        total = sum(values)
        # Money OBV: full-window signed value over exact reported value.
        # The first sign is based on the preceding observed bar, if available.
        if len(observations) >= period + 1:
            first = len(observations) - period
            pressure = sum(
                values[i] * (
                    1 if observations[first + i]["close"] >
                         observations[first + i - 1]["close"] else
                    -1 if observations[first + i]["close"] <
                          observations[first + i - 1]["close"] else 0)
                for i in range(period)
            ) / total if total > 0 else None
            price_change = 100 * (
                observations[-1]["close"] / observations[first - 1]["close"] - 1)
        else:
            pressure = None
            price_change = None
        answer[str(period)] = {
            "average_eok": round(total / period / 1e8, 3),
            "signed_money_pressure": round(pressure, 4)
            if pressure is not None else None,
            "price_return_pct": round(price_change, 2)
            if price_change is not None else None,
            "observations": period,
        }
    return answer


def observe(sessions, *, asof, exact_reported_amount=True):
    """Derive daily and full/partial weekly periods, not score the issuer.

    Sessions: ascending list of {date:'YYYY-MM-DD',close:KRW,amount:KRW}.
    Importing rows without an actual independently provided Amount is banned.
    """
    asof_day = _day(asof)
    if exact_reported_amount is not True:
        raise TurnoverHistoryError("proxy_amount_is_not_actual_trading_value")
    if type(sessions) is not list or not 20 <= len(sessions) <= MAX_ROWS:
        raise TurnoverHistoryError("insufficient_or_excessive_session_history")
    ordered = []
    last = None
    suspect_jump = False
    for row in sessions:
        if type(row) is not dict or set(row) != {"date", "close", "amount"}:
            raise TurnoverHistoryError("invalid_session_columns")
        current = _day(row["date"])
        if current > asof_day or (last is not None and
                                  (current <= last or (current - last).days > 10)):
            raise TurnoverHistoryError("unordered_or_gapped_actual_history")
        close = _number(row["close"], positive=True)
        amount = _number(row["amount"])
        if last is not None and abs(close / ordered[-1]["close"] - 1) > .55:
            suspect_jump = True
        ordered.append({"date": current, "close": close, "amount": amount})
        last = current
    if last != asof_day:
        raise TurnoverHistoryError("stale_actual_trading_value_series")
    weekly = []
    for row in ordered:
        key = row["date"].isocalendar()[:2]
        if weekly and weekly[-1]["iso_week"] == key:
            weekly[-1]["amount"] += row["amount"]
            weekly[-1]["close"] = row["close"]
            weekly[-1]["sessions"] += 1
        else:
            weekly.append({"iso_week": key, "amount": row["amount"],
                           "close": row["close"], "sessions": 1})
    # Weekly group is a calendar-week total, with short holiday weeks explicit.
    daily = _windows(ordered, DAILY_HORIZONS, "amount")
    by_week = _windows(weekly, WEEKLY_HORIZONS, "amount")
    return {
        "schema": SCHEMA,
        "asof": asof,
        "currency": "KRW",
        "amount_definition": "explicit_reported_historical_Amount_KRW",
        "daily": daily,
        "weekly": by_week,
        "available_daily_horizons": [h for h in DAILY_HORIZONS
                                     if daily[str(h)] is not None],
        "available_weekly_horizons": [h for h in WEEKLY_HORIZONS
                                      if by_week[str(h)] is not None],
        "source_sessions": len(ordered),
        "source_weeks": len(weekly),
        "latest_calendar_week_incomplete_possible": asof_day.weekday() != 4,
        "price_jump_corporate_action_review_required": suspect_jump,
        "official_regular_session_verified": False,
        "corporate_actions_verified": False,
        "investment_points": None,
        "scoring_allowed": False,
        "original_price_or_value_bars_exported": False,
    }
