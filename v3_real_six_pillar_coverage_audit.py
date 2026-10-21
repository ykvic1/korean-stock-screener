"""Offline six-area coverage audit from ALREADY SAVED market and DART facts.

No network or key use. Candidate counts are NOT verified full V3 scores.
2022-25 OpenDART annual snapshots are current-listed-company research, not
point-in-time adjusted backtest data or official foreign/institutional flow.
"""
from __future__ import annotations
import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path

from v3_dart_bulk_history import (
    SCHEMA as HISTORY_SCHEMA, SCORE_SCHEMA, _best_income_window,
    _three_year_margin, write_json
)

SCHEMA = "v3-real-saved-source-six-pillar-coverage-0.1"
FIELDS = ("ias","technical","financial","valuation","industry","momentum")
WEIGHTS = {"ias":20,"technical":25,"financial":15,
           "valuation":15,"industry":15,"momentum":10}

class CoverageAuditError(ValueError):
    """No provider/source rows or potentially sensitive contents in errors."""


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,
            ensure_ascii=False,separators=(",",":"),allow_nan=False
            ).encode("utf8")).hexdigest()


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def coverage(catalog,history,parts,weekly):
    day=catalog.get("date")
    if not isinstance(day,str):
        raise CoverageAuditError("missing_saved_market_date")
    try:
        if date.fromisoformat(day).isoformat()!=day:
            raise ValueError()
    except ValueError:
        raise CoverageAuditError("invalid_saved_market_date") from None
    if (history.get("schema")!=HISTORY_SCHEMA or
        parts.get("schema")!=SCORE_SCHEMA or
        weekly.get("schema")!="v3-seven-day-research-source-rotation-0.1" or
        history.get("asof")!=day or parts.get("asof")!=day or
        weekly.get("market_asof")!=day or
        history.get("historical_PIT_source_certified") is not False or
        history.get("four_year_complete_financial15_certified") is not False or
        parts.get("historical_PIT_certified") is not False or
        parts.get("all_scores_provisional_unverified") is not True or
        parts.get("fully_verified_six_pillar_count")!=0 or
        weekly.get("v3_live_full_six_pillar_score_claimed") is not False):
        raise CoverageAuditError("incompatible_saved_source_status")
    if (not isinstance(catalog.get("rows"),list) or
        not isinstance(history.get("records"),dict) or
        not isinstance(parts.get("rows"),list) or
        not isinstance(weekly.get("records"),dict)):
        raise CoverageAuditError("invalid_saved_source_collections")
    members={}
    for row in catalog["rows"]:
        code=row.get("code")
        if (not isinstance(code,str) or len(code)!=6 or
            not code.isascii() or not code.isdecimal() or
            row.get("market") not in ("KOSPI","KOSDAQ") or
            code in members):
            raise CoverageAuditError("invalid_or_duplicate_market_member")
        members[code]=row
    total=len(members)
    if (total<=0 or catalog.get("listed_total")!=total or
        history.get("listed_total")!=total or
        parts.get("listed_total")!=total or
        weekly.get("listed_total")!=total):
        raise CoverageAuditError("market_universe_count_mismatch")
    fiscal=history.get("fiscal_years")
    if (fiscal!=list(range(int(day[:4])-4,int(day[:4]))) or
        not all(isinstance(n,int) for n in fiscal)):
        raise CoverageAuditError("untrusted_four_fiscal_years")
    observed_years={str(y):0 for y in fiscal}
    attempted_years={str(y):0 for y in fiscal}
    complete4=set();valid3margin=set()
    book_rows=history["records"]
    if set(book_rows)-set(members):
        raise CoverageAuditError("unexpected_historical_issuer")
    for code,record in book_rows.items():
        if type(record) is not dict or type(record.get("years")) is not dict:
            raise CoverageAuditError("invalid_historical_issuer_years")
        years=record["years"]
        if set(years)-set(observed_years):
            raise CoverageAuditError("unexpected_historical_fiscal_year")
        for year,scopes in years.items():
            if type(scopes) is not dict or set(scopes)-{"CFS","OFS"}:
                raise CoverageAuditError("invalid_historical_fiscal_scopes")
            attempted_years[year]+=1
            if scopes:
                observed_years[year]+=1
        if _best_income_window(record,fiscal) is not None:
            complete4.add(code)
        if _three_year_margin(record,fiscal) is not None:
            valid3margin.add(code)
    if (history.get("four_year_income_observed_count")!=len(complete4) or
        history.get("completed_source_company_years")!=sum(attempted_years.values()) or
        history.get("observed_nonempty_company_years")!=sum(observed_years.values())):
        raise CoverageAuditError("historical_coverage_claim_mismatch")
    financial=set();valuation=set();pbr=set();earnings=set()
    margin_provisional=set();growth_provisional=set()
    has_independent_margin_parts=(
        "companies_with_computable_3y_margin_scores" in parts)
    if parts.get("companies_with_computable_4y_income_scores")!=len(complete4):
        raise CoverageAuditError("source_vs_research_financial_mismatch")
    seen_partial_codes=set()
    for row in parts["rows"]:
        code=row.get("code")
        if (code not in members or row.get("market")!=members[code]["market"]
            or row.get("asof")!=day or
            row.get("confirmed_v3_score") is not None or
            row.get("scoring_certified") is not False or
            row.get("financial_available_max") not in (0,4,8) or
            row.get("valuation_available_max") not in (0,5,10)):
            raise CoverageAuditError("untrusted_provisional_pillar_row")
        if code in seen_partial_codes:
            raise CoverageAuditError("duplicate_research_issuer")
        seen_partial_codes.add(code)
        comp=row.get("unverified_components")
        if type(comp) is not dict:
            raise CoverageAuditError("invalid_financial_component_records")
        weighted={
            "revenue_cagr_3y_pct":4,"operating_margin_3y_pct":4,
            "pbr":5,"earnings_yield_pct":5,
        }
        for metric,maximum in weighted.items():
            component=comp.get(metric)
            if type(component) is not dict:
                raise CoverageAuditError("invalid_financial_component_records")
            earned=component.get("candidate_points")
            expected=(maximum if earned is not None else 0)
            if (component.get("max_points")!=expected or
                (earned is not None and
                 (not _number(earned) or not 0<=earned<=maximum))):
                raise CoverageAuditError("invented_partial_component_points")
        expected_fin=sum(comp[k]["max_points"] for k in
                         ("revenue_cagr_3y_pct","operating_margin_3y_pct"))
        expected_val=sum(comp[k]["max_points"] for k in
                         ("pbr","earnings_yield_pct"))
        if (row["financial_available_max"]!=expected_fin or
            row["valuation_available_max"]!=expected_val or
            row.get("combined_available_max")!=expected_fin+expected_val or
            not _number(row.get("combined_provisional_points")) or
            abs(row["combined_provisional_points"]-sum(
                comp[k]["candidate_points"] or 0 for k in weighted))>0.00001 or
            not 0<expected_fin+expected_val<=18):
            raise CoverageAuditError("invented_partial_coverage_or_total")
        for key,prefix in (("financial","financial"),("valuation","valuation")):
            amount=row.get(prefix+"_candidate_points")
            possible=row.get(prefix+"_available_max")
            if (possible>0 and (not _number(amount) or amount<0 or amount>possible)
                or possible==0 and amount is not None):
                raise CoverageAuditError("untrusted_partial_pillar_points")
        if code in financial or code in valuation:
            raise CoverageAuditError("duplicate_research_issuer")
        growth=comp["revenue_cagr_3y_pct"]["candidate_points"]
        margin=(comp.get("operating_margin_3y_pct") or {}).get("candidate_points")
        if growth is not None:
            if code not in complete4:
                raise CoverageAuditError("growth_score_without_four_year_evidence")
            growth_provisional.add(code)
        if margin is not None:
            if code not in valid3margin:
                raise CoverageAuditError("margin_score_without_three_year_evidence")
            margin_provisional.add(code)
        if row["financial_available_max"]>0:
            if (code not in growth_provisional and
                code not in margin_provisional):
                raise CoverageAuditError("financial_score_without_source_component")
            financial.add(code)
        if row["valuation_available_max"]>0:
            valuation.add(code)
        if (comp.get("pbr") or {}).get("candidate_points") is not None:
            pbr.add(code)
        if (comp.get("earnings_yield_pct") or {}).get("candidate_points") is not None:
            earnings.add(code)
    if (len(growth_provisional)!=len(complete4) or
        (has_independent_margin_parts and
         parts.get("companies_with_computable_3y_margin_scores")!=
             len(margin_provisional)) or
        len(financial)!=(len(growth_provisional|margin_provisional)) or
        parts.get("companies_with_computable_pb_scores")!=len(pbr) or
        parts.get("companies_with_computable_earnings_yield_scores")!=len(earnings)):
        raise CoverageAuditError("research_component_evidence_count_mismatch")
    technical=set();momentum=set()
    if (weekly.get("processed_this_cycle")!=len(weekly["records"]) or
        weekly.get("unprocessed_this_cycle")!=total-len(weekly["records"]) or
        set(weekly["records"])-set(members)):
        raise CoverageAuditError("incompatible_weekly_coverage")
    # A 7-day rotating replay explicitly retains earlier *same-cycle*
    # issuer observations after the daily market census rolls forward.
    # Those old records remain valid as historical research references but
    # can NEVER be counted toward current-close technical/momentum points.
    # This cross-domain audit must not block fresh, independently verified
    # DART annual BS/IS source publication solely because of that lag.
    technical_source_all_dates=set()
    old_weekly_issuer_count=0
    for code,rec in weekly["records"].items():
        if not isinstance(rec,dict) or rec.get("code")!=code:
            raise CoverageAuditError("invalid_technical_source_identity")
        observed_asof=rec.get("market_source_asof")
        if type(observed_asof) is not str:
            raise CoverageAuditError("invalid_technical_source_identity")
        try:
            age=(date.fromisoformat(day)-date.fromisoformat(observed_asof)).days
        except ValueError:
            raise CoverageAuditError("invalid_technical_source_identity") from None
        if (date.fromisoformat(observed_asof).isoformat()!=observed_asof or
            age<0 or age>9):
            raise CoverageAuditError("invalid_technical_source_identity")
        current_day=age==0
        if not current_day:
            # Old observations are acceptable ONLY inside this week's
            # genuinely processed cohort and must preserve their own dates.
            if (rec.get("cycle")!=weekly.get("cycle_start") or
                type(rec.get("last_recalculated_kst")) is not str or
                rec["last_recalculated_kst"]>weekly.get("run_kst","") or
                rec["last_recalculated_kst"]<observed_asof):
                raise CoverageAuditError("invalid_technical_source_identity")
            old_weekly_issuer_count+=1
        tech_valid=(_number(rec.get("research_score_points")) and
            _number(rec.get("available_weight")) and
            0<rec["available_weight"]<=25 and
            0<=rec["research_score_points"]<=rec["available_weight"])
        if tech_valid:
            technical_source_all_dates.add(code)
            if current_day:technical.add(code)
        if (current_day and _number(rec.get("momentum_research_points")) and
            _number(rec.get("momentum_available_weight")) and
            0<rec["momentum_available_weight"]<=10 and
            0<=rec["momentum_research_points"]<=rec["momentum_available_weight"]):
            momentum.add(code)
    # The rotating workflow's own count is an observation over the full
    # active CYCLE, whereas the financial join below is exact MARKET DAY.
    if weekly.get("numeric_technical_candidates_this_cycle")!=len(technical_source_all_dates):
        raise CoverageAuditError("technical_source_count_disagrees")
    industry_labels={code for code,row in members.items()
                     if isinstance(row.get("sector"),str) and row["sector"].strip()}
    # Category labels are useful data but NOT independently scored industry
    # rotation, so their counts cannot become weighted industry=15 points.
    areas={"ias":set(),"technical":technical,"financial":financial,
           "valuation":valuation,"industry":set(),"momentum":momentum}
    four=technical&financial&valuation&momentum
    three_or_more={code for code in members if sum(code in areas[k] for k in
        ("technical","financial","valuation","momentum"))>=3}
    breakdown={}
    for name in ("KOSPI","KOSDAQ"):
        members_of={code for code in members if members[code]["market"]==name}
        breakdown[name]={
            "listed":len(members_of),
            "four_year_revenue_operating_profit":len(complete4&members_of),
            "technical_numeric_provisional":len(technical&members_of),
            "momentum_numeric_provisional":len(momentum&members_of),
            "financial_numeric_provisional":len(financial&members_of),
            "valuation_numeric_provisional":len(valuation&members_of),
            "four_unverified_numerical_domains_overlap":len(four&members_of),
        }
    result={
        "schema":SCHEMA,"market_asof":day,"listed_total":total,
        "weight_contract":dict(WEIGHTS),
        "six_pillar_numeric_provisional_issuer_counts":{
            name:len(areas[name]) for name in FIELDS},
        "six_pillar_official_complete_100pt_issuer_count":0,
        "three_or_more_provisional_numerical_areas":len(three_or_more),
        "four_numerical_areas_same_date_overlap_count":len(four),
        "four_numerical_areas_diagnostic_sample_codes":sorted(four)[:50],
        "industry_sector_label_issuer_count":len(industry_labels),
        "industry_scored_rotation_issuer_count":0,
        "official_investor_IAS_source_issuer_count":0,
        "technical_weekly_source_processed_issuer_count":len(weekly["records"]),
        "technical_weekly_prior_market_day_records_excluded":old_weekly_issuer_count,
        "historical_dart_distinct_issuer_count":len(book_rows),
        "historical_dart_company_years_attempted_by_fiscal":attempted_years,
        "historical_dart_company_years_with_accounts_by_fiscal":observed_years,
        "verified_four_year_income_reference_issuers":len(complete4),
        "three_year_actual_margin_source_issuers":len(valid3margin),
        "three_year_margin_provisional_score_issuers":len(margin_provisional),
        "official_pbr_verified_count":0,
        "simple_pbr_reference_provisional_issuers":len(pbr),
        "earnings_yield_reference_provisional_issuers":len(earnings),
        "market_breakdown":breakdown,
        "source_sha256_canonical":{
            "market_catalog":_canonical_hash(catalog),
            "dart_four_year_archive":_canonical_hash(history),
            "dart_partial_score":_canonical_hash(parts),
            "weekly_technical":_canonical_hash(weekly),
        },
        "limitations":[
            "Current-listed only: delisted/historical survivorship not resolved",
            "KRX historical raw OHLCV cannot be inferred from these files",
            "Technical and momentum candidates are unverified and unadjusted",
            "DART BS/IS research is not original historical point-in-time certified",
            "Simple PBR is NOT exchange-verified, financial and valuation partial",
            "IAS20 and industry rotation have zero verified numeric scoring source",
            "Four-domain sample is for engineering data-tests, NOT investment ranking",
            "Missing components have no zero scores and full model100 is unavailable",
        ],
        "buy_recommendations_authorized":False,
        "investment_grade_scores_confirmed":False,
    }
    return result


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog",default="docs/v22-market-catalog.json")
    ap.add_argument("--history",default="docs/v3-dart-bulk-history.json")
    ap.add_argument("--partials",default="docs/v3-dart-bulk-provisional.json")
    ap.add_argument("--weekly",default="docs/v3-weekly-rotation.json")
    ap.add_argument("--out",default="docs/v3-real-source-six-area-coverage.json")
    ap.add_argument("--mode",choices=("write","verify"),default="write")
    args=ap.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))
    result=coverage(read(args.catalog),read(args.history),
                    read(args.partials),read(args.weekly))
    target=Path(args.out)
    if args.mode=="verify":
        if not target.is_file() or read(target)!=result:
            raise CoverageAuditError("real_source_coverage_changed_after_rebase")
        print("real_saved_source_coverage_verified")
    else:
        write_json(target,result)
        print(json.dumps({
            "asof":result["market_asof"],
            "listed":result["listed_total"],
            "observed_4y_finance":result["verified_four_year_income_reference_issuers"],
            "four_area_test_sample":result["four_numerical_areas_same_date_overlap_count"],
            "six_area_full_scores":0,
        },sort_keys=True))


if __name__=="__main__":
    main()
