"""Replace unavailable investor identity with dated V2.2 price-volume evidence.

An explicitly NEW, provisional 'volume/OBV 20' domain. It NEVER claims
foreign/institutional accumulation, source-adjusted OHLCV, institutional IAS,
historical PIT, a confirmed 100-point score, or a trading recommendation.
Only the first 16/20 points have independently saved V2.2 observables:
  six: prior-40 vs most-recent-20 session volume participation
  six: 60-session signed-volume (OBV) slope DIRECTION
  four: price did not keep pace with rising OBV ([-12,+10]% over 60)
  four: 20-session post-spike absorption (NOT available in saved snapshot)
One-day volume surge is intentionally EXCLUDED because technical25 already
uses it (technical.volume_confirmation2).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from v22_full_universe_audit import audit_published_universe
from v3_dart_bulk_history import _stable_digest, write_json

SCHEMA="v3-current-market-volume-obv-provisional-research-0.1"
MAX_POINTS=20
OBSERVABLE_MAX=16
SUBMAX={"sustained_volume":6,"obv_60_direction":6,
        "obv_price_not_reflected":4,"post_spike_absorption":4}

class VolumeEvidenceError(ValueError):
    """Fixed error labels, no original price rows in exception messages."""


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def observed(stock, *, asof, market):
    """Return small derived score, or None if dated input isn't trustworthy."""
    if (not isinstance(stock,dict) or stock.get("price_date")!=asof
        or stock.get("market")!=market):
        return None
    ratio=stock.get("volume_ratio")
    rising=stock.get("obv_rising")
    divergence=stock.get("divergence")
    price60=stock.get("price60")
    if (not _finite(ratio) or not 0<=ratio<=100
        or type(rising) is not bool or type(divergence) is not bool
        or not _finite(price60) or not -100<=price60<=10000):
        return None
    # The saved V2.2 divergence is exactly (OBV slope>0 AND price60 in
    # [-12, 10]). If a malformed/mismatched row violates this, do not score.
    if divergence is not (rising and -12<=price60<=10):
        return None
    # Sustained, not one-session, participation. Excessive 20-day surge
    # earns LESS: panic volume/exhaustion is not evidence of quiet accumulation.
    vol_score=(6 if 1.25<=ratio<=3 else
               4 if 1.05<=ratio<1.25 else
               2 if 0.85<=ratio<1.05 else
               1 if ratio>3 else 0)
    obv_score=6 if rising else 0
    lag_score=4 if divergence else 0
    risks=[]
    if ratio>3:
        risks.append("excess_20day_volume_not_accumulation")
    if price60<-12:
        risks.append("price_downtrend_requires_support_confirmation")
    if price60>30:
        risks.append("60day_price_extension_chase_risk")
    if not rising:
        risks.append("obv_not_up")
    scores={"sustained_volume":vol_score,"obv_60_direction":obv_score,
            "obv_price_not_reflected":lag_score,
            "post_spike_absorption":None}
    points=sum(value for value in scores.values() if value is not None)
    return {
        "asof":asof,"market":market,"observed_points":points,
        "observed_available_max":OBSERVABLE_MAX,"unobserved_max":4,
        "subpoints":scores,"risk_flags":risks,
        "source_history_adjusted_verified":False,
        "source_exchange_regular_only_verified":False,
        "foreign_institutional_net_buy_inferred":False,
        "investable_score_certified":False,
        "trading_signal_authorized":False,
    }


def build(market,catalog):
    """One CURRENT market-date census, all unpriced issuers still missing."""
    try:
        audit=audit_published_universe(market)
    except (ValueError,TypeError,KeyError):
        raise VolumeEvidenceError("invalid_v22_daily_source") from None
    day=audit["asof"]
    if (type(catalog) is not dict or
        catalog.get("schema")!="v22-current-listed-catalog-0.1" or
        catalog.get("date")!=day or
        catalog.get("listed_total")!=audit["market_listing_size"] or
        type(catalog.get("rows")) is not list or
        len(catalog["rows"])!=audit["market_listing_size"]):
        raise VolumeEvidenceError("market_catalog_source_day_mismatch")
    members={}
    for entry in catalog["rows"]:
        if (type(entry) is not dict or type(entry.get("code")) is not str
            or len(entry["code"])!=6 or not entry["code"].isdigit()
            or entry["code"] in members or
            entry.get("market") not in ("KOSPI","KOSDAQ")):
            raise VolumeEvidenceError("invalid_listed_issuer_identity")
        members[entry["code"]]=entry
    results={}
    source_usable=0
    for stock in market["stocks"]:
        code=stock["code"]
        item=members.get(code)
        if (not isinstance(item,dict) or
            item.get("market")!=stock["market"] or
            item.get("name")!=stock["name"] or
            item.get("price_history_status")!="priced"):
            raise VolumeEvidenceError("priced_issuer_catalog_mismatch")
        source_usable+=1
        score=observed(stock,asof=day,market=item["market"])
        if score is not None:
            results[code]=score
    if source_usable!=market["candidate_count"] or len(results)>source_usable:
        raise VolumeEvidenceError("volume_observation_count_mismatch")
    return {
        "schema":SCHEMA,"asof":day,"listed_total":len(members),
        "price_source_issuers":source_usable,
        "observed_volume_candidate_issuers":len(results),
        "insufficient_or_unpriced_issuers":len(members)-len(results),
        "points_budget":MAX_POINTS,
        "available_point_budget_per_observed_issuer":OBSERVABLE_MAX,
        "not_observed_absorption_max":4,
        "weight_contract_in_this_model":{
            "volume":20,"technical":25,"financial":15,
            "valuation":15,"industry":15,"momentum":10},
        "subweight_contract":dict(SUBMAX),
        "ias_investor_flow_component_used":False,
        "technical_one_day_volume_not_double_counted":True,
        "source_market_sha256":_stable_digest(market),
        "source_catalog_sha256":_stable_digest(catalog),
        "source_raw_ohlcv_published":False,
        "corporate_actions_adjusted":False,
        "official_foreign_institutional_flow_verified":False,
        "historical_PIT_certified":False,
        "certified_100_point_investment_scores":0,
        "research_only":True,
        "records":dict(sorted(results.items())),
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs",default="docs")
    p.add_argument("--mode",choices=("write","verify"),default="write")
    x=p.parse_args(argv)
    root=Path(x.docs)
    def read(name):
        return json.loads((root/name).read_text(encoding="utf8"))
    value=build(read("data.json"),read("v22-market-catalog.json"))
    path=root/"v3-volume-obv-observed.json"
    if x.mode=="write":
        write_json(path,value)
    else:
        if not path.is_file() or read(path)!=value:
            raise VolumeEvidenceError("volume_source_changed_after_rebase")
    print(json.dumps({"status":"verified_source_volume_obv_research_only",
       "asof":value["asof"],"listed":value["listed_total"],
       "priced_source":value["price_source_issuers"],
       "volume_scored":value["observed_volume_candidate_issuers"],
       "available_max_of_20":OBSERVABLE_MAX,
       "certified_100_point_investment_scores":0},sort_keys=True))


if __name__=="__main__":
    main()
