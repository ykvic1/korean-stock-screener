"""Exploratory sector-turnaround 15-point attribution; NEVER live V3 industry.

Receives ONLY results of evaluate_industry_cohort, already full-roster and
decision-date gated. This is a stand-alone *research* comparison to understand
whether a 15-point industry dimension can provide useful differentiation,
not permission to amend the existing 100-point model or award any issuer
investment score. No API, ticker ranking, prices or account rows are output.

User-approved 15-point industry breakdown (part of six-area /100):
  early sector fund rotation signal:   5
  industry earnings floor and recovery:4
  issuer earnings improvement breadth: 3
  overheating and concentration:       3
Unverified data stays unscored; approved weights never certify a buy signal.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date
import math
import re

SCHEMA="v3-industry-transition-risk-stage-preview-0.1"
WEIGHTS={
    "early_rotation":5,
    "profit_transition":4,
    "improvement_diffusion":3,
    "overheat_crowding_quality":3,
}
MIN_SECTORS=5


class IndustryTransitionError(ValueError):
    """No raw source values or ticker identities in errors."""


def _num(value):
    return type(value) in (int,float) and math.isfinite(value)


def preview(sectors, *, asof, min_sectors=MIN_SECTORS):
    if (type(asof) is not str or
        not re.fullmatch(r"\d{4}-\d\d-\d\d",asof)):
        raise IndustryTransitionError("invalid_sector_asof")
    try:
        date.fromisoformat(asof)
    except ValueError:
        raise IndustryTransitionError("invalid_sector_asof") from None
    if (type(min_sectors) is not int or min_sectors<MIN_SECTORS or
        min_sectors>30 or type(sectors) is not list or len(sectors)>1000):
        raise IndustryTransitionError("invalid_sector_cohort_count")
    accepted={}
    for item in sectors:
        if (type(item) is not dict or
            item.get("schema")!="v3-industry-raw-cycle-0.1" or
            item.get("asof")!=asof or
            item.get("market") not in ("KOSPI","KOSDAQ") or
            type(item.get("industry_code")) is not str or
            re.fullmatch(r"[A-Za-z0-9._-]{2,30}",item["industry_code"]) is None or
            item.get("v3_scoring_authorized") is not False or
            item.get("confirmed_buy") is not False or
            item.get("sector_membership_original_pit_verified") is not False or
            item.get("annual_operating_profit_original_pit_verified") is not False or
            item.get("price_corporate_action_and_session_verified") is not False or
            item.get("full_delisted_sector_universe_authenticated") is not False or
            item.get("official_market_sector_definition_verified") is not False or
            item.get("only_declared_cohort_checked") is not True or
            type(item.get("declared_member_count")) is not int or
            item["declared_member_count"]<2 or
            type(item.get("features")) is not dict):
            raise IndustryTransitionError("untrusted_sector_research_evidence")
        key=(item["market"],item["industry_code"])
        if key in accepted:
            raise IndustryTransitionError("duplicate_sector_snapshot")
        if not isinstance(item.get("declared_member_identities"),list) or (
            len(item["declared_member_identities"])!=
            item["declared_member_count"]):
            raise IndustryTransitionError("incomplete_sector_roster_evidence")
        features=item["features"]
        spec=[
            ("industry_profit_growth_12m_pct",-100,100000),
            ("industry_profit_improving_issuer_ratio",0,1),
            ("industry_breadth_above_ma_ratio",0,1),
            ("industry_recovery_largest_issuer_delta_share",0,1),
            ("industry_early_rotation_signal",0,1),
            ("industry_overheat_concentration_quality",0,1),
        ]
        for k,low,high in spec:
            n=features.get(k)
            if n is not None and (not _num(n) or not low<=n<=high):
                raise IndustryTransitionError("out_of_range_sector_source")
        profit_complete=(item.get("profit_covered_issuers")==
                         item["declared_member_count"])
        price_complete=(item.get("breadth_covered_issuers")==
                        item["declared_member_count"])
        if not profit_complete and any(features.get(k) is not None for k in
            ("industry_profit_growth_12m_pct",
             "industry_profit_improving_issuer_ratio",
             "industry_recovery_largest_issuer_delta_share",
             "industry_overheat_concentration_quality")):
            raise IndustryTransitionError("partial_roster_profit_score_forbidden")
        if not price_complete and any(features.get(k) is not None for k in
            ("industry_breadth_above_ma_ratio",
             "industry_early_rotation_signal",
             "industry_overheat_concentration_quality")):
            raise IndustryTransitionError("partial_roster_price_score_forbidden")
        if (features.get("industry_profit_improving_issuer_ratio")==0 and
            features.get("industry_recovery_largest_issuer_delta_share") is not None):
            raise IndustryTransitionError("invented_positive_profit_growth")
        accepted[key]=item
    cohorts=defaultdict(list)
    for (market,_),item in accepted.items():
        cohorts[market].append(item)
    output=[]
    with_component={key:0 for key in WEIGHTS}
    for (market,sector),item in sorted(accepted.items()):
        features=item["features"]
        peers=cohorts[market]
        eligible=len(peers)>=min_sectors
        components={key:None for key in WEIGHTS}
        if eligible:
            signal=features.get("industry_early_rotation_signal")
            if signal is not None:
                components["early_rotation"]=round(5*signal,6)
            grow=features.get("industry_profit_growth_12m_pct")
            # Negative earnings: *observed* zero points, not a missing value.
            # 10% thresholds are trial hypotheses, not calibrated targets.
            if grow is not None:
                components["profit_transition"]=(
                    0 if grow<=0 else 1 if grow<10 else
                    3 if grow<25 else 4)
            breadth=features.get("industry_profit_improving_issuer_ratio")
            if breadth is not None:
                components["improvement_diffusion"]=(
                    0 if breadth<0.3 else 1 if breadth<0.5 else
                    2 if breadth<0.7 else 3)
            quality=features.get("industry_overheat_concentration_quality")
            if quality is not None:
                components["overheat_crowding_quality"]=round(3*quality,6)
        available=sum(WEIGHTS[k] for k,v in components.items()
                      if v is not None)
        points=sum(v for v in components.values() if v is not None)
        for k,v in components.items():
            if v is not None:
                with_component[k]+=1
        output.append({
            "market":market,"industry_code":sector,"asof":asof,
            "declared_member_count":item["declared_member_count"],
            "eligible_same_market_sectors":len(peers),
            "provisional_15point_parts":components,
            "provisional_observed_score":points if available else None,
            "provisional_available_weight":available,
            "provisional_missing_weight":15-available,
            "sector_member_roster_pit_verified":False,
            "all_sector_historical_constituents_verified":False,
            "industry_5433_weights_user_approved":True,
            "research_scoring_not_yet_provider_certified":True,
            "stock_buy_signal":False,
            "investment_grade_industry15_score":None,
        })
    return {
        "schema":SCHEMA,"asof":asof,
        "approved_industry15_weights":dict(WEIGHTS),
        "min_sectors_per_market":min_sectors,
        "input_sector_snapshots":len(accepted),
        "research_only_sectors":output,
        "observed_issuer_investment_scores":0,
        "full_six_pillar_issuer_scores_certified":0,
        "source_historical_pit_verified":False,
        "approved_for_live_stock_scoring":False,
        "component_sector_coverage":with_component,
        "no_sector_member_raw_records_exported":True,
    }
