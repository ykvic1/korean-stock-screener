"""Bounded daily-price-path observations for *future* 4+7 research factors.

These are raw-provider-derived, unadjusted, and not an independent KRX
regular-session or corporate-action-verified source. No additional V3 points
are authorized. Persist small aggregates only, never price/volume bars.
"""
from __future__ import annotations

from datetime import date
import math

SCHEMA="v3-unscored-price-path-evidence-0.1"


def _number(value,*,positive=False):
    if isinstance(value,bool):
        return False
    try:
        number=float(value)
    except (TypeError,ValueError,OverflowError):
        return False
    return math.isfinite(number) and (number>0 if positive else number>=0)


def observe(close,volume,*,asof,session_days=None):
    """Only completed lookback windows; no forward return or zero-filling.

    The input is already day-sorted by the existing full-market OHLCV reader.
    A zero-volume/suspended session invalidates these path observations; it
    must not be interpreted as a negative momentum or an absorption score.
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
        any(not _number(v,positive=True) for v in volumes)):
        return None
    closes=[float(x) for x in closes];volumes=[float(v) for v in volumes]
    if any(abs(closes[i]/closes[i-1]-1)>0.5 for i in range(1,121)):
        return None  # Potential unadjusted share-split/missing-session artifact.
    up20=sum(closes[i]>closes[i-1] for i in range(101,121))
    # Exactly twelve NON-OVERLAPPING five-interval windows, spanning 60
    # daily close changes. Not another 20/60-day relative-return estimate.
    positive_5day=sum(closes[i]>closes[i-5] for i in range(65,121,5))
    prior60=100*(closes[60]/closes[0]-1)
    recent60=100*(closes[120]/closes[60]-1)
    if any(not math.isfinite(v) or abs(v)>1e6 for v in (prior60,recent60)):
        return None
    # Look for an observed strong high-volume UP session (not a distribution
    # bar), followed by at least five complete sessions of price hold and
    # reduced volume. This remains a research FLAG, not 4 tradeable points.
    evaluated=0;absorbed=0
    for i in range(101,116):
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
        "up_close_days20":up20,
        "positive_nonoverlapping_five_interval_blocks60":positive_5day,
        "prior60_close_change_pct":round(prior60,6),
        "recent60_close_change_pct":round(recent60,6),
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
        ("up_close_days20",20),
        ("positive_nonoverlapping_five_interval_blocks60",12),
        ("complete_post_spike_events_evaluated",15),
        ("post_spike_price_hold_with_volume_easing_events",15),
    )
    if any(type(item.get(k)) is not int or not 0<=item[k]<=limit
           for k,limit in bounds):
        return False
    if (item["post_spike_price_hold_with_volume_easing_events"]>
        item["complete_post_spike_events_evaluated"]):
        return False
    return all(type(item.get(k)) in (int,float) and
               math.isfinite(item[k]) and abs(item[k])<=1e6
               for k in ("prior60_close_change_pct","recent60_close_change_pct"))
