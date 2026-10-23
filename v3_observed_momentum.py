"""Partial 8/15 V3 momentum research from existing relative 20/60-day inputs.

The source market snapshot supplies relative20/relative60 derived from
stock-vs-index returns, but not the full dated raw index bars. Therefore
benchmark date/corporate action evidence is not independently verified,
the additional 7-point breadth and persistence factors are UNAVAILABLE, and scores are
never certified. Peers use same-market, same-source-stock-day midranks.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
import math

SCHEMA = "v3-observed-market-momentum-8of10-research-0.1"
WEIGHTS = {"relative20": 4, "relative60": 4, "positive_days20": 2,
           "trend_persistence60": 3, "reversal_breadth120": 2}
MIN_PEERS = 80


def numeric(v):
    return type(v) in (int, float) and math.isfinite(v) and -100 <= v <= 1000


def market_momentum(source, *, min_peers=MIN_PEERS):
    """Return research data by stock code; never extrapolate absent fields."""
    asof = source.get("date")
    try:
        if date.fromisoformat(asof).isoformat() != asof:
            return {}
    except (ValueError, TypeError):
        return {}
    if not isinstance(source.get("stocks"), list) or not 8 <= min_peers <= 1000:
        return {}
    allowed = source.get("index_available")
    if not isinstance(allowed, list):
        return {}
    usable = [s for s in source["stocks"] if isinstance(s,dict)
        and s.get("market") in ("KOSPI","KOSDAQ")
        and s["market"] in allowed
        and s.get("price_date") == asof
        and isinstance(s.get("code"),str)]
    peers = {
        (m,k): sorted(float(s[k]) for s in usable
                     if s["market"]==m and numeric(s.get(k)))
        for m in ("KOSPI","KOSDAQ")
        for k in ("relative20","relative60")
    }
    if sum(WEIGHTS.values())!=15:
        raise ValueError("invalid_momentum_five_pillar_budget")
    candidates={}
    for s in usable:
        parts={}
        available=0
        for field,max_points in (("relative20",4),("relative60",4)):
            array=peers[(s["market"],field)]
            val=s.get(field)
            if not numeric(val) or len(array)<min_peers:
                parts[field]=None
                continue
            pct=(bisect_left(array,float(val))+bisect_right(array,float(val)))/(2*len(array))
            parts[field]=round(max_points*pct,6)
            available+=max_points
        parts["positive_days20"]=None
        parts["trend_persistence60"]=None
        parts["reversal_breadth120"]=None
        score=round(sum(v for v in parts.values() if v is not None),6) if available else None
        candidates[s["code"]]={
            "schema":SCHEMA,"asof":asof,"market":s["market"],
            "momentum_parts":parts, "momentum_observed_maximum":available,
            "momentum_research_candidate":score,"momentum_maximum":15,
            "peer20_count":len(peers[(s["market"],"relative20")]),
            "peer60_count":len(peers[(s["market"],"relative60")]),
            "market_benchmark_date_independently_verified":False,
            "source_unadjusted_corporate_events_verified":False,
            "positive_days20_observed":False,
            "investable_score":None,"buy_certified":False,
        }
    return candidates
