"""Bounded daily-price-path observations for *future* 4+7 research factors.

These are raw-provider-derived, unadjusted, and not an independent KRX
regular-session or corporate-action-verified source. No additional V3 points
are authorized. Persist small aggregates only, never price/volume bars.
"""
from __future__ import annotations

from datetime import date
import math

SCHEMA="v3-unscored-price-path-evidence-0.1"
UNAVAILABLE_REASONS = frozenset({
    "insufficient_121_sessions",
    "missing_or_mismatched_session_index",
    "irregular_or_stale_session_calendar",
    "nonpositive_or_invalid_price_volume",
    "zero_volume_close_changed_without_trade",
    "possible_unadjusted_corporate_action",
    "implausible_two_window_return",
    "other_path_validation_failure",
})


def valid_unavailable_reason(observation, reason):
    """New-format scans require a fixed reason only when a path is missing."""
    if observation is not None:
        return reason is None
    return type(reason) is str and reason in UNAVAILABLE_REASONS


def classify_unavailable(close, volume, *, asof, session_days=None):
    """Source-in-memory diagnostic only; rejected paths NEVER gain points."""
    try:
        day = date.fromisoformat(asof)
        if day.isoformat() != asof:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("unscored_path_invalid_asof") from None
    if len(close) < 121:
        return "insufficient_121_sessions"
    if len(close) != len(volume) or session_days is None or len(session_days) != len(close):
        return "missing_or_mismatched_session_index"
    dates = []
    for raw in session_days[-121:]:
        if callable(getattr(raw, "date", None)):
            dates.append(raw.date())
        elif type(raw) is date:
            dates.append(raw)
        else:
            return "missing_or_mismatched_session_index"
    if dates[-1] != day or any(
        not 0 < (b - a).days <= 10 for a, b in zip(dates, dates[1:])
    ):
        return "irregular_or_stale_session_calendar"
    closes = list(close[-121:])
    volumes = list(volume[-121:])
    if (any(not _number(x, positive=True) for x in closes)
            or any(not _number(x, positive=False) for x in volumes)
            or not _number(volumes[0], positive=True)
            or not _number(volumes[-1], positive=True)):
        return "nonpositive_or_invalid_price_volume"
    closes = [float(x) for x in closes]
    volumes = [float(x) for x in volumes]
    if any(volumes[i] == 0 and closes[i] != closes[i-1]
           for i in range(1, 121)):
        return "zero_volume_close_changed_without_trade"
    if any(abs(closes[i] / closes[i-1] - 1) > 0.5 for i in range(1, 121)):
        return "possible_unadjusted_corporate_action"
    for p in (100 * (closes[60] / closes[0] - 1),
              100 * (closes[120] / closes[60] - 1)):
        if not math.isfinite(p) or abs(p) > 1e6:
            return "implausible_two_window_return"
    return "other_path_validation_failure"



def _number(value,*,positive=False):
    if isinstance(value,bool):
        return False
    try:
        number=float(value)
    except (TypeError,ValueError,OverflowError):
        return False
    return math.isfinite(number) and (number>0 if positive else number>=0)


def close_path_facts(closes):
    """Calculate the four shared price-only facts for a 121-session path."""
    if len(closes) != 121 or any(not _number(x, positive=True) for x in closes):
        return None
    closes = [float(x) for x in closes]
    if any(abs(b / a - 1) > .5 for a, b in zip(closes, closes[1:])):
        return None
    prior = 100 * (closes[60] / closes[0] - 1)
    recent = 100 * (closes[120] / closes[60] - 1)
    if any(not math.isfinite(v) or abs(v) > 1e6 for v in (prior, recent)):
        return None
    return {
        "up_close_days20":sum(closes[i] > closes[i-1] for i in range(101, 121)),
        "positive_nonoverlapping_five_interval_blocks60":sum(
            closes[i] > closes[i-5] for i in range(65, 121, 5)),
        "prior60_close_change_pct":round(prior, 6),
        "recent60_close_change_pct":round(recent, 6),
    }


def valid_close_path_facts(item):
    return (type(item) is dict
            and type(item.get("up_close_days20")) is int
            and 0 <= item["up_close_days20"] <= 20
            and type(item.get("positive_nonoverlapping_five_interval_blocks60")) is int
            and 0 <= item["positive_nonoverlapping_five_interval_blocks60"] <= 12
            and all(type(item.get(k)) in (int, float) and math.isfinite(item[k])
                    and abs(item[k]) <= 1e6
                    for k in ("prior60_close_change_pct", "recent60_close_change_pct")))


def observe(close,volume,*,asof,session_days=None):
    """Only dated completed lookbacks; never synthesize missing bars/trades.

    A *recorded* historical zero-volume bar is usable for the price path
    only when its close exactly carries forward the prior recorded close.
    Such a bar cannot qualify as a spike, absorption, or liquidity signal.
    """
    try:
        day=date.fromisoformat(asof)
        if day.isoformat()!=asof:
            raise ValueError()
    except (TypeError,ValueError):
        raise ValueError("unscored_path_invalid_asof") from None
    if (len(close)!=len(volume) or len(close)<121 or
        session_days is None or len(session_days)!=len(close)):
        return None
    dates=[]
    for raw in session_days[-121:]:
        if callable(getattr(raw,"date",None)):
            dates.append(raw.date())
        elif type(raw) is date:
            dates.append(raw)
        else:
            return None
    if (dates[-1]!=day or any(not 0<(b-a).days<=10
                              for a,b in zip(dates,dates[1:]))):
        return None
    closes=list(close[-121:]);volumes=list(volume[-121:])
    if (any(not _number(x,positive=True) for x in closes) or
        any(not _number(v,positive=False) for v in volumes) or
        not _number(volumes[0],positive=True) or
        not _number(volumes[-1],positive=True)):
        return None
    closes=[float(x) for x in closes];volumes=[float(v) for v in volumes]
    if any(volumes[i]==0 and closes[i]!=closes[i-1]
           for i in range(1,121)):
        return None
    facts = close_path_facts(closes)
    if facts is None:
        return None
    # Look for an observed strong high-volume UP session (not a distribution
    # bar), followed by at least five complete sessions of price hold and
    # reduced volume. This remains a research FLAG, not 4 tradeable points.
    evaluated=0;absorbed=0
    for i in range(101,116):
        # No-trade sessions cannot create a high-volume event or artificially
        # lower the pre-spike baseline or five-session post-spike average.
        if any(v==0 for v in volumes[i-20:i+6]):
            continue
        base=sum(volumes[i-20:i])/20
        if base<=0 or volumes[i]/base<2.0 or closes[i]<closes[i-1]:
            continue
        evaluated+=1
        after=volumes[i+1:i+6]
        if (len(after)==5 and sum(after)/5<=0.85*volumes[i] and
            min(closes[i+1:i+6])>=0.94*closes[i] and
            closes[i+5]>=0.97*closes[i]):
            absorbed+=1
    return {
        "schema":SCHEMA,"asof":asof,
        "path_window_trading_sessions":121,
        **facts,
        "complete_post_spike_events_evaluated":evaluated,
        "post_spike_price_hold_with_volume_easing_events":absorbed,
        "raw_ohlcv_exported":False,
        "source_regular_session_verified":False,
        "corporate_actions_adjusted_verified":False,
        "market_index_path_verified":False,
        "new_v3_available_points":0,
        "investment_score_certified":False,
        "interpretation":"unscored_dated_source_observation_only",
    }


def valid_observation(item,asof):
    """Reject invented availability or score when passing batched receipts."""
    if item is None:
        return True  # Genuinely unusable/suspended path remains unavailable.
    if type(item) is not dict:
        return False
    expected={
        "schema","asof","path_window_trading_sessions","up_close_days20",
        "positive_nonoverlapping_five_interval_blocks60",
        "prior60_close_change_pct","recent60_close_change_pct",
        "complete_post_spike_events_evaluated",
        "post_spike_price_hold_with_volume_easing_events",
        "raw_ohlcv_exported","source_regular_session_verified",
        "corporate_actions_adjusted_verified","market_index_path_verified",
        "new_v3_available_points","investment_score_certified",
        "interpretation",
    }
    if (set(item)!=expected or item.get("schema")!=SCHEMA or
        item.get("asof")!=asof or
        item.get("path_window_trading_sessions")!=121 or
        item.get("raw_ohlcv_exported") is not False or
        item.get("source_regular_session_verified") is not False or
        item.get("corporate_actions_adjusted_verified") is not False or
        item.get("market_index_path_verified") is not False or
        type(item.get("new_v3_available_points")) is not int or
        item.get("new_v3_available_points")!=0 or
        item.get("investment_score_certified") is not False or
        item.get("interpretation")!="unscored_dated_source_observation_only"):
        return False
    bounds=(
        ("complete_post_spike_events_evaluated",15),
        ("post_spike_price_hold_with_volume_easing_events",15),
    )
    if any(type(item.get(k)) is not int or not 0<=item[k]<=limit
           for k,limit in bounds):
        return False
    if (item["post_spike_price_hold_with_volume_easing_events"]>
        item["complete_post_spike_events_evaluated"]):
        return False
    return valid_close_path_facts(item)
