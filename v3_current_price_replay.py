"""Replay same-day V3 technical25 and momentum8 directly from saved prices.

New DART/KRX API calls: ZERO. No 350-issuer acquisition batch is changed.
Recomputing 2,497 *already saved* observations offline does not recollect
a single price. All scores are provisional and not corporate-action/PIT
certified; missing subparts remain null, never multiplied to the full weight.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from v3_volume_participation import build as audit_market
from v3_dart_bulk_history import _stable_digest
from v3_observed_momentum import market_momentum,SCHEMA as MOMENTUM
from v3_weekly_rotation import technical_points,existing_evidence_adapter

SCHEMA="v3-all-listed-current-market-price-only-technical-momentum-replay-0.1"
TECH_SCHEMA="v3-pattern-within-six-pillars-research-0.4"

class CurrentPriceReplayError(ValueError):
    """Static descriptions only; never disclose original price/provider rows."""


def build(market,catalog):
    verified=audit_market(market,catalog)
    asof=verified["asof"]
    roster={r["code"]:r for r in catalog["rows"]}
    momo=market_momentum(market)
    results={}
    tc=mc=0
    for stock in market["stocks"]:
        code=stock["code"]
        member=roster[code]
        if member["market"]!=stock["market"] or stock.get("price_date")!=asof:
            raise CurrentPriceReplayError("same_date_price_market_identity_invalid")
        value,available,_=technical_points(stock,asof)
        tech,_=existing_evidence_adapter(stock,asof)
        technical=None
        if available:
            if (type(tech) is not dict or tech.get("schema")!=TECH_SCHEMA or
                tech.get("asof")!=asof or
                tech.get("technical_observed_maximum")!=available or
                tech.get("technical_maximum")!=25 or
                tech.get("buy_certified") is not False or
                tech.get("v3_release_ready") is not False or
                tech.get("investable_score") is not None or
                not isinstance(tech.get("technical_parts"),dict)):
                raise CurrentPriceReplayError("invalid_price_technical_source")
            # Full source-derived subparts are retained because they're
            # already independently present in market daily research.
            technical={key:tech[key] for key in (
                "schema","asof","technical_maximum","technical_weights",
                "technical_parts","technical_observed_maximum",
                "technical_research_candidate","buy_certified",
                "v3_release_ready","investable_score")}
            tc+=1
        mom=momo.get(code)
        valid_momentum=(type(mom) is dict
            and mom.get("schema")==MOMENTUM
            and mom.get("asof")==asof and mom.get("market")==member["market"]
            and mom.get("momentum_observed_maximum") in (4,8)
            and mom.get("momentum_research_candidate") is not None
            and mom.get("buy_certified") is False
            and mom.get("investable_score") is None
            and mom.get("source_unadjusted_corporate_events_verified") is False
            and mom.get("market_benchmark_date_independently_verified") is False)
        if valid_momentum:mc+=1
        if technical is not None or valid_momentum:
            results[code]={
                "code":code,"market":member["market"],"asof":asof,
                "technical_research_reference":technical,
                "momentum_research_reference":mom if valid_momentum else None,
                "official_investment_score_certified":False,
                "price_source_corporate_action_verified":False,
            }
    return {
        "schema":SCHEMA,"asof":asof,
        "listed_total":verified["listed_total"],
        "priced_current_session":verified["price_source_issuers"],
        "technical_research_issuer_count":tc,
        "momentum_research_issuer_count":mc,
        "at_least_one_price_factor_issuer_count":len(results),
        "source_market_sha256":_stable_digest(market),
        "source_catalog_sha256":_stable_digest(catalog),
        "separate_source_provider_calls":0,
        "official_ias_or_buyer_identity_inferred":False,
        "corporate_action_adjusted_verified":False,
        "historical_PIT_certified":False,
        "sector_score_authentic":False,
        "certified_100point_investment_issuers":0,
        "rows":dict(sorted(results.items()))
    }


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--docs",default="docs")
    a.add_argument("--mode",choices=("write","verify"),default="write")
    args=a.parse_args(argv)
    root=Path(args.docs)
    def read(p):
        return json.loads((root/p).read_text(encoding="utf8"))
    report=build(read("data.json"),read("v22-market-catalog.json"))
    dest=root/"v3-current-price-technical-momentum.json"
    if args.mode=="write":
        from v3_dart_bulk_history import write_json
        write_json(dest,report)
    else:
        if not dest.is_file() or json.loads(dest.read_text(encoding="utf8"))!=report:
            raise CurrentPriceReplayError("price_replay_changed_after_rebase")
    print(json.dumps({k:report[k] for k in (
        "asof","listed_total","priced_current_session",
        "technical_research_issuer_count","momentum_research_issuer_count",
        "at_least_one_price_factor_issuer_count",
        "separate_source_provider_calls","certified_100point_investment_issuers"
    )},sort_keys=True))


if __name__=="__main__":
    main()
