"""Bounded two-market PBR-reference feasibility research.

One get_market_fundamental_by_ticker call per market. Only aggregate counts
leave memory. Never publish vendor stock-level values or investment scores.
"""
from __future__ import annotations
from datetime import date
import json
import math
from pathlib import Path
import re
from typing import Callable

SCHEMA="v22-current-listed-catalog-0.1"
MARKETS=("KOSPI","KOSDAQ")
MAX_STOCKS=5000
DAY=re.compile(r"\d{4}-\d{2}-\d{2}\Z")
CODE=re.compile(r"\d{6}\Z")

class PBRPilotError(ValueError):pass

def _valid_pbr(value)->bool:
    try:
        if type(value) is bool or value is None:return False
        n=float(value)
        return math.isfinite(n) and 0<n<1000
    except (ValueError,TypeError,OverflowError):
        return False

def _catalog(data,asof):
    if (not isinstance(data,dict) or data.get("schema")!=SCHEMA or
        data.get("date")!=asof or not isinstance(data.get("rows"),list)
        or len(data["rows"])!=data.get("listed_total") or
        not 1000<=len(data["rows"])<=MAX_STOCKS):
        raise PBRPilotError("bad_source_day_or_full_stock_catalog")
    result={market:set() for market in MARKETS}
    for row in data["rows"]:
        if (not isinstance(row,dict) or row.get("market") not in MARKETS or
            not isinstance(row.get("code"),str) or
            CODE.fullmatch(row["code"]) is None or
            row["code"] in result[row["market"]]):
            raise PBRPilotError("bad_duplicate_or_market_identity")
        result[row["market"]].add(row["code"])
    if not all(len(result[m])>200 for m in MARKETS):
        raise PBRPilotError("incomplete_market_listing")
    return result

def audit_market_pbr(asof:str,catalog:dict,*,provider:Callable):
    if type(asof) is not str or not DAY.fullmatch(asof):
        raise PBRPilotError("bad_asof")
    try:date.fromisoformat(asof)
    except ValueError:
        raise PBRPilotError("bad_asof") from None
    listed=_catalog(catalog,asof)
    calls=0
    summary={}
    total_observed=0
    failures=[]
    for market in MARKETS:
        calls+=1
        try:
            frame=provider(asof.replace("-",""),market=market)
            if (frame is None or not hasattr(frame,"columns") or
                not hasattr(frame,"index") or "PBR" not in frame.columns or
                not 0<len(frame)<=MAX_STOCKS):
                raise PBRPilotError("no_valid_pbr_frame")
            matches=0
            seen=set()
            observed=0
            # Count only symbols still listed in the matching market.
            for code_raw,value in zip(frame.index,frame["PBR"]):
                code=str(code_raw)
                if CODE.fullmatch(code) is None or code in seen:
                    raise PBRPilotError("invalid_or_duplicate_provider_stock")
                seen.add(code)
                if code in listed[market]:
                    matches+=1
                    if _valid_pbr(value):observed+=1
            if matches==0 or matches>len(listed[market]):
                raise PBRPilotError("no_current_market_match")
            total_observed+=observed
            summary[market]={
                "current_listed":len(listed[market]),
                "matched_provider_listing":matches,
                "observed_positive_finite_pbr":observed,
                "missing_or_unusable_pbr":len(listed[market])-observed,
            }
        except Exception:
            # Provider error body may contain URLs, API tokens or quotes.
            failures.append(market)
            summary[market]={
                "current_listed":len(listed[market]),
                "status":"provider_unavailable_or_unverified",
                "observed_positive_finite_pbr":0,
            }
    return {
      "schema":"v3-two-market-website-pbr-observability-0.1",
      "asof":asof,"status":"observed_research_only" if not failures else
                      "partial_or_blocked_provider_research",
      "markets_requested":2,"provider_calls":calls,
      "current_listed_total":sum(len(x) for x in listed.values()),
      "current_listed_observed_pbr":total_observed,
      "market_breakdown":summary,
      "unavailable_markets":failures,
      "data_source_day_independently_verified":False,
      "automated_redistribution_permission_verified":False,
      "official_per_stock_pbr_verified":False,
      "pbr_became_buy_score":False,
      "raw_provider_pbr_saved":False,
      "raw_provider_stock_ticker_saved":False,
      "historical_point_in_time_verified":False,
    }

def main():
    from pykrx import stock
    asof="2026-10-19"
    catalog=json.loads(Path("docs/v22-market-catalog.json").read_text(encoding="utf-8"))
    output=audit_market_pbr(asof,catalog,provider=stock.get_market_fundamental_by_ticker)
    print(json.dumps(output,ensure_ascii=False,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
