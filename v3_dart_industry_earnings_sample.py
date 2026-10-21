"""Offline current-DART-industry x verified annual operating-profit evidence.

Research cohort diagnostics only. "induty_code" is CURRENT corporate metadata,
not historical exchange sectors, and until all members of every dated group
are mapped, the resulting earnings breadth is **sample-only**. No part of
the sector15 or six-pillar V3/100 is allocated here. No historical PIT claims.

Never print individual issuer operating income or full issuer/source rows.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from v3_dart_rolling_book_reference import _inputs
from v3_dart_industry_codes import prior_records
from v3_dart_bulk_history import (
    SCHEMA as BULK_SCHEMA, _day, _valid_receipt,
    _stable_digest, _validate_prior, write_json,
)

SCHEMA="v3-observed-dart-current-sector-profit-sample-0.1"
MIN_GROUP_ISSUERS_FOR_AGGREGATE=3


class IndustryEarningsError(ValueError):
    """Safe provenance/consistency reason codes only."""


def _pair(record,previous,latest,asof):
    """Two fiscal-year annual profits, one per-year receipt, SAME CFS/OFS."""
    if type(record) is not dict:
        return None
    years=record.get("years")
    if type(years) is not dict:
        return None
    for scope in ("CFS","OFS"):
        amounts=[]
        for year in (previous,latest):
            fiscal=years.get(str(year),{})
            if type(fiscal) is not dict:
                break
            entry=fiscal.get(scope,{})
            if type(entry) is not dict or type(entry.get("values")) is not dict:
                break
            values=entry["values"]
            amount=values.get("operating_income")
            revenue=values.get("revenue")
            if (type(amount) is not int or type(revenue) is not int or
                revenue<=0 or abs(amount)>10**18 or
                not _valid_receipt(entry.get("receipt_id"),year,_day(asof)) or
                "operating_income" in entry.get("ambiguous",[]) or
                "revenue" in entry.get("ambiguous",[])):
                break
            amounts.append(amount)
        if len(amounts)==2:
            return (scope,amounts[0],amounts[1])
    return None


def build(market,catalog,industry,bulk):
    asof,members=_inputs(market,catalog)
    if (type(industry) is not dict or type(bulk) is not dict or
        bulk.get("schema")!=BULK_SCHEMA or bulk.get("asof")!=asof or
        bulk.get("listed_total")!=len(members) or
        bulk.get("historical_PIT_source_certified") is not False or
        bulk.get("raw_source_rows_published") is not False or
        bulk.get("fiscal_years")!=
        list(range(_day(asof).year-4,_day(asof).year))):
        raise IndustryEarningsError("incompatible_official_financial_provenance")
    try:
        classified=prior_records(industry,asof,members)
        retained=_validate_prior(bulk,asof,members)
    except (ValueError,KeyError,TypeError):
        raise IndustryEarningsError("untrusted_industry_or_financial_source") from None
    if (industry.get("official_krx_sector_membership_verified") is not False or
        industry.get("past_constituent_membership_pit_verified") is not False or
        industry.get("industry_5433_points_authorized") is not False):
        raise IndustryEarningsError("unverified_sector_score_provenance")
    previous,latest=_day(asof).year-2,_day(asof).year-1
    cohort=defaultdict(lambda:{"mapped":0,"reported":0,"improved":0,
                               "scopes":set(),"sum_prior":0,"sum_latest":0,
                               "loss_to_profit":0})
    matched_issuers=0
    for code,ref in classified.items():
        full=ref.get("industry_code")
        if full is None:
            continue
        # First-two characters are an ALPHANUMERIC CODE-PREFIX diagnostic,
        # NOT a validated official KRX industry group or a historical roster.
        label=(members[code]["market"],full[:2])
        group=cohort[label]
        group["mapped"]+=1
        observed=_pair(retained.get(code),previous,latest,asof)
        if observed is None:
            continue
        scope,old,new=observed
        group["reported"]+=1
        group["improved"]+=int(new>old)
        group["loss_to_profit"]+=int(old<=0<new)
        group["scopes"].add(scope)
        group["sum_prior"]+=old
        group["sum_latest"]+=new
        matched_issuers+=1

    grouped=[]
    observed_sample_count=0
    for (market_name,prefix),g in sorted(cohort.items()):
        anonymous=g["reported"]>=MIN_GROUP_ISSUERS_FOR_AGGREGATE
        # Aggregate P&L cannot claim like-for-like accounting scope if
        # consolidated and standalone statements coexist in this group.
        consistent=anonymous and len(g["scopes"])==1
        improving=(round(g["improved"]/g["reported"],6)
                   if anonymous else None)
        observed_sample_count+=int(anonymous)
        grouped.append({
            "market":market_name,"dart_industry_code_prefix_2":prefix,
            "current_classified_issuers":g["mapped"],
            "issuer_year_pair_observed":g["reported"],
            "unobserved_income_among_known_codes":g["mapped"]-g["reported"],
            "two_year_scope_mix":(sorted(g["scopes"]) if anonymous else []),
            "observed_earnings_improving_issuer_ratio_sample_only":improving,
            "observed_loss_to_profit_turnarounds_sample_only":
                g["loss_to_profit"] if anonymous else None,
            "observed_pair_scope_uniform":consistent,
            "sample_aggregate_operating_profit_growth_pct":
                (round(100*(g["sum_latest"]/g["sum_prior"]-1),6)
                 if consistent and g["sum_prior"]>0 else None),
            "sample_aggregate_prior_loss_to_latest_profit":
                (g["sum_prior"]<=0<g["sum_latest"] if consistent else None),
            "group_is_approved_historical_industry_cohort":False,
            "group_15point_investment_score":None,
            "missing_marketwide_membership_cannot_be_rescaled":True,
        })
    return {
        "schema":SCHEMA,"asof":asof,"fiscal_year_pair":[previous,latest],
        "current_listed_total":len(members),
        "dart_current_industry_codes_observed":
            sum(v.get("industry_code") is not None
                for v in classified.values()),
        "mapped_industry_issuer_earnings_two_year_matched":matched_issuers,
        "observed_2digit_code_market_groups":len(grouped),
        "groups_with_at_least_three_real_earnings_pairs":observed_sample_count,
        "groups":[*grouped],
        "full_market_industry_membership_pit_verified":False,
        "sector_15_points_earned_issuer_count":0,
        "industry_subweights_locked":{"early_rotation":5,"profit_transition":4,
            "improvement_diffusion":3,"overheat_crowding_quality":3},
        "source_sha256":{
            "catalog":_stable_digest(catalog),
            "industry_current_company_codes":_stable_digest(industry),
            "dart_verified_major_account":_stable_digest(bulk),
        },
        "source_usage_notice":"Sampled current-company DART industry-code prefix and saved annual reports; not an official KRX sector trend, PIT universe, or approved issuer score.",
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs",default="docs")
    p.add_argument("--mode",choices=("write","verify"),default="write")
    opt=p.parse_args(argv)
    d=Path(opt.docs)
    def read(name):
        return json.loads((d/name).read_text(encoding="utf8"))
    snap=build(read("data.json"),read("v22-market-catalog.json"),
        read("v3-dart-industry-classification.json"),
        read("v3-dart-bulk-history.json"))
    dest=d/"v3-dart-industry-earnings-sample.json"
    if opt.mode=="write":
        write_json(dest,snap)
    else:
        if not dest.exists() or read(dest.name)!=snap:
            raise IndustryEarningsError("industry_research_source_stale")
    print(json.dumps({"status":"observed_sample_unscored",
        "asof":snap["asof"],
        "current_industry_codes":snap["dart_current_industry_codes_observed"],
        "real_two_year_profit_pairs":
            snap["mapped_industry_issuer_earnings_two_year_matched"],
        "sample_groups":snap["groups_with_at_least_three_real_earnings_pairs"],
        "investment_15point_scores_earned":0},sort_keys=True))


if __name__=="__main__":
    main()
