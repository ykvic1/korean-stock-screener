"""Publish an ALL-LISTED, observed-only six-pillar V3 research score CSV.

No foreign/institutional IAS or industry score is inferred from a chart,
legacy v2.2 score, name, sector label or a blank. FCF is an independently
validated 4/5-point subfactor, not an additional 15-point category.
Historical PIT, corporate actions, full investment score remain unverified.
Only a source-derived CSV and aggregate summary are written to public Pages.
"""
from __future__ import annotations
import argparse
import csv
from io import StringIO
import json
import math
from pathlib import Path

from v3_dart_bulk_history import _stable_digest
from v3_score_contract import INDUSTRY_POINTS
from v3_real_six_pillar_coverage_audit import coverage, CoverageAuditError
from v3_dart_cashflow_factor_preview import score_preview, FCFPreviewError
from v3_dart_cashflow_enrichment import CashflowError
from v3_dart_industry_codes import prior_records, IndustrySourceError

AREAS=("ias","technical","financial","valuation","industry","momentum")
# The user approved the six-domain 100-point contract and industry 5/4/3/3.
# No five-domain 85-point alternative or rescaling is used.
INDUSTRY_PARTS=dict(INDUSTRY_POINTS)
WEIGHTS={"ias":20,"technical":25,"financial":15,
         "valuation":15,"industry":15,"momentum":10}
NAMES={"ias":"수급","technical":"기술","financial":"재무",
       "valuation":"가치","industry":"산업","momentum":"모멘텀"}
SCHEMA="v3-all-current-listed-observed-partial-scorecard-0.1"


class FullMarketScorecardError(ValueError):
    """Fixed messages, never print credentials, raw market or company data."""


def _number(value, limit=100):
    return (type(value) in (float,int) and math.isfinite(value) and
            0<=value<=limit)


def _contribution(earned, available, limit):
    if available==0 and earned is None:
        return None,0
    if (type(available) is not int or available<=0 or available>limit or
            not _number(earned,available)):
        raise FullMarketScorecardError("unverified_factor_weight_or_points")
    return float(earned),available


def _momentum(record, asof, market):
    ref=record.get("momentum_research_reference")
    if (not isinstance(ref,dict) or
        ref.get("schema")!="v3-observed-market-momentum-8of10-research-0.1" or
        ref.get("asof")!=asof or ref.get("market")!=market or
        ref.get("buy_certified") is not False or
        ref.get("investable_score") is not None or
        ref.get("source_unadjusted_corporate_events_verified") is not False or
        ref.get("market_benchmark_date_independently_verified") is not False or
        ref.get("positive_days20_observed") is not False):
        return None,0
    parts=ref.get("momentum_parts")
    if not isinstance(parts,dict) or parts.get("positive_days20") is not None:
        return None,0
    avail=0
    points=0
    for key,peer in (("relative20","peer20_count"),("relative60","peer60_count")):
        v=parts.get(key)
        if v is None:
            continue
        if not _number(v,4) or type(ref.get(peer)) is not int or ref[peer]<80:
            return None,0
        avail+=4
        points+=v
    if (avail not in (4,8) or record.get("momentum_available_weight")!=avail or
        ref.get("momentum_observed_maximum")!=avail or
        ref.get("momentum_maximum")!=10 or
        not _number(record.get("momentum_research_points"),avail) or
        not _number(ref.get("momentum_research_candidate"),avail) or
        abs(points-record["momentum_research_points"])>0.00001 or
        abs(points-ref["momentum_research_candidate"])>0.00001):
        return None,0
    return _contribution(record["momentum_research_points"],avail,10)


def generate(market,catalog,history,parts,weekly,cash=None,fcf=None,
             industry_source=None):
    # This audit crosschecks exact issuer/annual/year/market/scoring constraints.
    audit=coverage(catalog,history,parts,weekly)
    day=audit["market_asof"]
    listed=audit["listed_total"]
    members={r["code"]:r for r in catalog["rows"]}
    raw_parts={r["code"]:r for r in parts["rows"]}
    industry_codes={}
    industry_ref_status="source_not_available"
    if industry_source is not None:
        try:
            # Exact current KOSPI/KOSDAQ issuer and source digest. Historical
            # sector membership is NOT proven by today's company.json.
            industry_codes=prior_records(industry_source,day,members)
            if industry_source.get("official_krx_sector_membership_verified") is not False or
                industry_source.get("past_constituent_membership_pit_verified") is not False or
                industry_source.get("industry_5433_points_authorized") is not False:
                raise FullMarketScorecardError("industry_profile_claims_pit")
            industry_ref_status="verified_current_company_classification_only"
        except (IndustrySourceError,FullMarketScorecardError,TypeError,ValueError):
            industry_codes={}
            industry_ref_status="source_rejected"
    fcf_rows={}
    fcf_effective=None
    fcf_accepted=False
    fcf_reason="source_not_available"
    if cash is not None and fcf is not None:
        try:
            if (market.get("date")!=day or
                market.get("eligible_count")!=listed or
                fcf.get("asof")!=day or fcf.get("listed_total")!=listed or
                fcf.get("joined_to_main_v3_ranking") is not False or
                fcf.get("hist_PIT_verified") is not False or
                fcf.get("six_pillar_confirmed_100pt") is not None or
                fcf.get("buy_recommendations_authorized") is not False or
                fcf.get("source_same_filing_scope_receipt_checked") is not True or
                fcf.get("raw_source_rows_stored") is not False or
                fcf.get("four_year_financial15_complete") is not False or
                fcf.get("valuation15_complete") is not False or
                not isinstance(fcf.get("rows"),list) or
                any(type(r) is not dict or
                    r.get("scoring_certified") is not False or
                    r.get("historical_PIT_certified") is not False or
                    r.get("strict_receipt_scope_matched") is not True
                    for r in fcf["rows"])):
                raise FullMarketScorecardError("fcf_source_day_or_claim_mismatch")
            # Historical major-account backfill and CF collection finish at
            # different times. Recalculate from the SAVED verified cashflow
            # filings against today's same-scope major annual receipts rather
            # than dropping true FCF just because the previous preview hash
            # predates unrelated newly collected companies. No new API calls.
            expected=score_preview(
                market,catalog,history,cash,
                minimum_peers=fcf.get("minimum_same_market_peers",30))
            fcf_rows={r["code"]:r for r in expected["rows"]}
            if len(fcf_rows)!=len(expected["rows"]):
                raise FullMarketScorecardError("duplicate_fcf_issuer")
            fcf_accepted=True
            fcf_effective=expected
            fcf_reason=("same_receipt_verified_research_only" if expected==fcf
                         else "recomputed_from_saved_verified_cashflow")
        except (CashflowError,FCFPreviewError,FullMarketScorecardError,
                KeyError,TypeError,ValueError):
            # Do NOT take down all-listed BS/IS/technical reporting just
            # because the independently scheduled cashflow source is stale.
            fcf_rows={}
            fcf_reason="fcf_unavailable_until_source_reconciliation"
    rows=[]
    count={name:0 for name in AREAS}
    zero=0
    known_industry_labels=0
    for code in sorted(members):
        member=members[code]
        values={k:(None,0) for k in AREAS}
        part=raw_parts.get(code)
        if part:
            for area in ("financial","valuation"):
                values[area]=_contribution(
                    part[area+"_candidate_points"],
                    part[area+"_available_max"],WEIGHTS[area])
        rec=weekly["records"].get(code)
        if rec and rec.get("market_source_asof")==day and rec.get(
                "six_pillar_research_score_confirmed") is False:
            tech_avail=rec.get("available_weight")
            tech_score=rec.get("research_score_points")
            if (type(tech_avail) is int and 0<tech_avail<=25 and
                _number(tech_score,tech_avail) and
                rec.get("no_data_construed_as_zero") is True):
                values["technical"]=_contribution(tech_score,tech_avail,25)
            values["momentum"]=_momentum(rec,day,member["market"])
        f=fcf_rows.get(code)
        if f and f.get("market")==member["market"] and f.get("asof")==day:
            for area,field,maximum in (
                    ("financial","financial_fcf_assets",4),
                    ("valuation","valuation_fcf_yield",5)):
                more_max=f.get(field+"_available_max")
                more_points=f.get(field+"_candidate_points")
                if more_max==maximum and _number(more_points,maximum):
                    base,base_weight=values[area]
                    if base_weight+maximum>WEIGHTS[area]:
                        raise FullMarketScorecardError("cashflow_double_count_guard")
                    values[area]=_contribution(
                        (base or 0)+more_points,base_weight+maximum,WEIGHTS[area])
        available=sum(v[1] for v in values.values())
        earned=sum(v[0] for v in values.values() if v[0] is not None)
        if available==0:zero+=1
        for a,v in values.items():
            if v[1]:count[a]+=1
        # Industry may be useful as a descriptive label; do not confuse it
        # with a historical, scored, industry-cycle or peer-strength signal.
        label=member.get("sector")
        label=(label.strip() if type(label) is str and
               0<len(label.strip())<=100 else None)
        known_industry_labels+=int(label is not None)
        rows.append({"code":code,"name":member["name"],"market":member["market"],
                     "price_status":member.get("price_history_status"),
                     "industry_label_unverified":label,
                     "dart_current_industry_code":(
                         industry_codes.get(code,{}).get("industry_code")),
                     "factors":values,"earned":round(earned,4) if available else None,
                     "available":available,"unavailable":100-available})
    if (len(rows)!=listed or count["ias"]!=0 or count["industry"]!=0 or
        any(r["available"]>100 for r in rows)):
        raise FullMarketScorecardError("false_full_score_or_missing_listed_issuer")
    summary={
        "schema":SCHEMA,"asof":day,"listed_total":listed,
        "score_state":"provisional_observed_only_not_investment_grade",
        "full_six_pillar_certified_count":0,
        "missing_data_never_zero_filled":True,
        "all_listed_included_even_without_price":True,
        "weights":dict(WEIGHTS),
        "industry_approved_subweights":dict(INDUSTRY_PARTS),
        "industry_numeric_score_requires_real_verified_inputs":True,
        "industry_labels_descriptive_unverified":known_industry_labels,
        "industry_current_company_source_status":industry_ref_status,
        "industry_code_source_attempted":len(industry_codes),
        "industry_code_source_observed":sum(
            r["industry_code"] is not None for r in industry_codes.values()),
        "industry_code_source_sector15_earned_count":0,
        "provisional_numeric_issuer_counts":count,
        "no_numeric_factor_issuer_count":zero,
        "at_least_one_numeric_factor_count":listed-zero,
        "fcf_source_state":fcf_reason,
        "fcf_source_added":fcf_accepted,
        "source_sha256":{
            "catalog":_stable_digest(catalog),
            "dart_major":_stable_digest(history),
            "dart_partial":_stable_digest(parts),
            "weekly":_stable_digest(weekly),
            "fcf":_stable_digest(fcf_effective) if fcf_accepted else None,
            "industry_company_profile":_stable_digest(industry_source)
                if industry_ref_status=="verified_current_company_classification_only"
                else None,
        },
        "notice":"Missing factor columns are blank, never 0. No official IAS20, sector15, historical PIT or verified buy ranking."
    }
    return rows,summary


def to_csv(rows,summary):
    cols=["종목코드","종목명","시장","시장자료기준일","가격자료상태"]
    for name in AREAS:
        cols += [NAMES[name]+"_잠정점수",NAMES[name]+"_산출배점"]
    cols+=["V3잠정합계","산출가능배점_100점중","자료미확보배점_100점중",
           "산업_분류_참고용","DART_현재업종코드_산업점수아님",
           "정식투자점수","인증상태"]
    output=StringIO(newline="")
    writer=csv.writer(output,lineterminator="\n")
    writer.writerow(cols)
    def fmt(v):
        return "" if v is None else (format(v,".4f").rstrip("0").rstrip(".") or "0")
    for r in rows:
        current=[r["code"],r["name"],r["market"],summary["asof"],
                 r["price_status"]]
        for name in AREAS:
            earned,avail=r["factors"][name]
            current.extend([fmt(earned),avail if avail else ""])
        current += [fmt(r["earned"]),r["available"],r["unavailable"],
                    r["industry_label_unverified"] or "",
                    r["dart_current_industry_code"] or "",
                    "", "미검증·연구용"]
        writer.writerow(current)
    return "\ufeff"+output.getvalue()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs",default="docs")
    p.add_argument("--mode",choices=("write","verify"),default="write")
    a=p.parse_args(argv)
    d=Path(a.docs)
    def read(name):
        return json.loads((d/name).read_text(encoding="utf8"))
    market=read("data.json")
    catalog=read("v22-market-catalog.json")
    history=read("v3-dart-bulk-history.json")
    parts=read("v3-dart-bulk-provisional.json")
    weekly=read("v3-weekly-rotation.json")
    cash_path=d/"v3-dart-cashflow-reference.json"
    fcf_path=d/"v3-dart-fcf-research-preview.json"
    cash=read(cash_path.name) if cash_path.is_file() else None
    fcf=read(fcf_path.name) if fcf_path.is_file() else None
    industry_path=d/"v3-dart-industry-classification.json"
    industry=read(industry_path.name) if industry_path.is_file() else None
    rows,summary=generate(market,catalog,history,parts,weekly,cash,fcf,
                          industry)
    content=to_csv(rows,summary)
    dest=d/"v3-all-listed-scores.csv"
    report=d/"v3-all-listed-scorecard-summary.json"
    record=json.dumps(summary,sort_keys=True,ensure_ascii=False,
                      separators=(",",":"),allow_nan=False)+"\n"
    if a.mode=="verify":
        if (not dest.is_file() or not report.is_file() or
            dest.read_text(encoding="utf8")!=content or
            report.read_text(encoding="utf8")!=record):
            raise FullMarketScorecardError("scorecard_input_changed_after_rebase")
        print("all_listed_observed_scorecard_verified")
    else:
        dest.write_text(content,encoding="utf8")
        report.write_text(record,encoding="utf8")
        print(json.dumps({"listed":summary["listed_total"],
             "with_observed_points":summary["at_least_one_numeric_factor_count"],
             "source_by_area":summary["provisional_numeric_issuer_counts"],
             "fcf_joined":summary["fcf_source_added"],
             "industry_subweights":summary["industry_approved_subweights"],
             "certified_full":0},
             sort_keys=True))


if __name__=="__main__":
    main()
