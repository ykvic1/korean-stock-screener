"""Publish an ALL-LISTED, observed-only six-pillar V3 research score CSV.

Investor IAS is replaced in this NEW scorecard by research-only volume/OBV
candidate observations (at most 16 of the 20-point budget initially).
No foreign/institutional accumulation or sector score is inferred from a chart,
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
from v3_volume_participation import build as observed_volume, OBSERVABLE_MAX
from v3_current_price_replay import SCHEMA as PRICE_REPLAY_SCHEMA
from v3_weekly_rotation import TECH_PARTS

AREAS=("volume","technical","financial","valuation","industry","momentum")
# The user approved the six-domain 100-point contract and industry 5/4/3/3.
# No five-domain 85-point alternative or rescaling is used.
INDUSTRY_PARTS=dict(INDUSTRY_POINTS)
WEIGHTS={"volume":20,"technical":25,"financial":15,
         "valuation":15,"industry":15,"momentum":10}
NAMES={"volume":"거래량·OBV","technical":"기술","financial":"재무",
       "valuation":"가치","industry":"산업","momentum":"모멘텀"}
SCHEMA="v3-all-current-listed-observed-partial-scorecard-0.2-volume-replaces-ias"


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
    if (not isinstance(parts,dict) or parts.get("positive_days20") is not None or
        parts.get("trend_persistence60") is not None or
        parts.get("reversal_breadth120") is not None):
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
        ref.get("momentum_maximum") not in (10,15) or
        not _number(record.get("momentum_research_points"),avail) or
        not _number(ref.get("momentum_research_candidate"),avail) or
        abs(points-record["momentum_research_points"])>0.00001 or
        abs(points-ref["momentum_research_candidate"])>0.00001):
        return None,0
    return _contribution(record["momentum_research_points"],avail,10)



def _replay_technical(ref, asof):
    """Use only independently observed technical subparts, never a 25-point guess."""
    if ref is None:
        return None,0
    if (type(ref) is not dict or
        ref.get("schema")!="v3-pattern-within-six-pillars-research-0.4" or
        ref.get("asof")!=asof or ref.get("technical_maximum")!=25 or
        ref.get("buy_certified") is not False or
        ref.get("v3_release_ready") is not False or
        ref.get("investable_score") is not None or
        ref.get("technical_weights")!=TECH_PARTS):
        raise FullMarketScorecardError("price_replay_technical_contract_invalid")
    parts=ref.get("technical_parts")
    if type(parts) is not dict or set(parts)!=set(TECH_PARTS):
        raise FullMarketScorecardError("price_replay_technical_parts_invalid")
    avail=0
    earned=0.
    for name,maximum in TECH_PARTS.items():
        value=parts[name]
        if value is None:
            continue
        if not _number(value,maximum):
            raise FullMarketScorecardError("price_replay_technical_part_out_of_range")
        avail+=maximum
        earned+=value
    if avail==0 or ref.get("technical_observed_maximum")!=avail:
        raise FullMarketScorecardError("price_replay_technical_availability_invalid")
    candidate=ref.get("technical_research_candidate")
    if candidate is not None and (avail!=25 or
        not _number(candidate,25) or abs(candidate-earned)>0.01):
        raise FullMarketScorecardError("price_replay_technical_total_invalid")
    return _contribution(round(earned,4),avail,25)


def _verified_price_replay(source,market,catalog,day,members):
    """Reject stale/malformed 0-call replay before joining ANY issuer."""
    if source is None:
        return {},"source_not_available"
    try:
        if (type(source) is not dict or
            source.get("schema")!=PRICE_REPLAY_SCHEMA or
            source.get("asof")!=day or
            source.get("listed_total")!=len(members) or
            source.get("source_market_sha256")!=_stable_digest(market) or
            source.get("source_catalog_sha256")!=_stable_digest(catalog) or
            source.get("separate_source_provider_calls")!=0 or
            source.get("official_ias_or_buyer_identity_inferred") is not False or
            source.get("corporate_action_adjusted_verified") is not False or
            source.get("historical_PIT_certified") is not False or
            source.get("sector_score_authentic") is not False or
            source.get("certified_100point_investment_issuers")!=0 or
            type(source.get("rows")) is not dict):
            raise FullMarketScorecardError("price_replay_source_mismatch")
        refs=source["rows"]
        if (source.get("at_least_one_price_factor_issuer_count")!=len(refs) or
            type(source.get("priced_current_session")) is not int or
            not len(refs)<=source["priced_current_session"]<=len(members)):
            raise FullMarketScorecardError("price_replay_issuer_count_mismatch")
        normalized={}
        tc=mc=0
        for code,ref in refs.items():
            if (code not in members or type(ref) is not dict or
                ref.get("code")!=code or ref.get("asof")!=day or
                ref.get("market")!=members[code]["market"] or
                ref.get("official_investment_score_certified") is not False or
                ref.get("price_source_corporate_action_verified") is not False):
                raise FullMarketScorecardError("price_replay_issuer_identity_invalid")
            tech_ref=ref.get("technical_research_reference")
            mom_ref=ref.get("momentum_research_reference")
            technical=_replay_technical(tech_ref,day)
            momentum=(None,0)
            if mom_ref is not None:
                if type(mom_ref) is not dict:
                    raise FullMarketScorecardError("price_replay_momentum_invalid")
                temp={"momentum_research_reference":mom_ref,
                      "momentum_available_weight":mom_ref.get("momentum_observed_maximum"),
                      "momentum_research_points":mom_ref.get("momentum_research_candidate")}
                momentum=_momentum(temp,day,members[code]["market"])
                if not momentum[1]:
                    raise FullMarketScorecardError("price_replay_momentum_contract_invalid")
            if not technical[1] and not momentum[1]:
                raise FullMarketScorecardError("price_replay_empty_issuer")
            tc+=int(bool(technical[1]))
            mc+=int(bool(momentum[1]))
            normalized[code]={"technical":technical,"momentum":momentum}
        if (source.get("technical_research_issuer_count")!=tc or
            source.get("momentum_research_issuer_count")!=mc):
            raise FullMarketScorecardError("price_replay_factor_count_mismatch")
        return normalized,"same_market_digest_verified_research_only"
    except (FullMarketScorecardError,TypeError,ValueError,KeyError):
        return {},"source_rejected_or_stale"


def generate(market,catalog,history,parts,weekly,cash=None,fcf=None,
             industry_source=None,price_replay=None):
    # This audit crosschecks exact issuer/annual/year/market/scoring constraints.
    audit=coverage(catalog,history,parts,weekly)
    day=audit["market_asof"]
    listed=audit["listed_total"]
    members={r["code"]:r for r in catalog["rows"]}
    raw_parts={r["code"]:r for r in parts["rows"]}
    # Reuse only same-day v2.2 price-derived volume/OBV fields.
    # An independent live investor-identity API is NOT required.
    volume_source=observed_volume(market,catalog)
    volume_rows=volume_source["records"]
    replay_rows,replay_state=_verified_price_replay(
        price_replay,market,catalog,day,members)
    industry_codes={}
    industry_ref_status="source_not_available"
    if industry_source is not None:
        try:
            # Exact current KOSPI/KOSDAQ issuer and source digest. Historical
            # sector membership is NOT proven by today's company.json.
            industry_codes=prior_records(industry_source,day,members)
            if (industry_source.get("official_krx_sector_membership_verified") is not False
                or industry_source.get("past_constituent_membership_pit_verified") is not False
                or industry_source.get("industry_5433_points_authorized") is not False):
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
        volume=volume_rows.get(code)
        if volume:
            values["volume"]=_contribution(
                volume["observed_points"],volume["observed_available_max"],20)
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
        # Prefer broader exact-current-price coverage over the 350-name
        # weekly rotation; substitute, never add the same pillar twice.
        replayed=replay_rows.get(code)
        if replayed:
            for area in ("technical","momentum"):
                if replayed[area][1]>=values[area][1] and replayed[area][1]>0:
                    values[area]=replayed[area]
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
    if (len(rows)!=listed or count["volume"]!=len(volume_rows) or count["industry"]!=0 or
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
        "volume20_replaces_unavailable_investor_IAS20":True,
        "legacy_foreign_institutional_IAS_verified_count":0,
        "volume20_observed_maximum_per_issuer":OBSERVABLE_MAX,
        "volume20_unobserved_absorption_points":4,
        "technical_one_day_volume_not_double_counted":True,
        "technical_momentum_price_replay_state":replay_state,
        "technical_momentum_price_replay_issuer_count":len(replay_rows),
        "price_replay_new_provider_calls":0,
        "industry_current_company_source_status":industry_ref_status,
        "industry_code_source_attempted":len(industry_codes),
        "industry_code_source_observed":sum(
            r["industry_code"] is not None for r in industry_codes.values()),
        "industry_code_source_sector15_earned_count":0,
        "provisional_numeric_issuer_counts":count,
        # Counts from the same source-validated snapshot used to calculate
        # the CSV; do not confuse attempted annual requests with 4-year
        # complete filings or receipt-matched FCF with investment certification.
        "observed_source_depth":{
            "four_year_dart_attempted_issuers":
                audit["historical_dart_distinct_issuer_count"],
            "four_year_income_observed_issuers":
                audit["verified_four_year_income_reference_issuers"],
            "three_year_margin_observed_issuers":
                audit["three_year_actual_margin_source_issuers"],
            "three_year_receipt_matched_fcf_issuers":(
                cash["metrics"]["three_year_fcf_receipt_matched_issuers"]
                if fcf_accepted else None),
            "current_company_industry_code_issuers":
                sum(r["industry_code"] is not None
                    for r in industry_codes.values()),
        },
        "no_numeric_factor_issuer_count":zero,
        "at_least_one_numeric_factor_count":listed-zero,
        "fcf_source_state":fcf_reason,
        "fcf_source_added":fcf_accepted,
        "source_sha256":{
            "catalog":_stable_digest(catalog),
            "dart_major":_stable_digest(history),
            "dart_partial":_stable_digest(parts),
            "weekly":_stable_digest(weekly),
            "same_day_price_replay":_stable_digest(price_replay)
                if replay_state=="same_market_digest_verified_research_only"
                else None,
            "volume_observed":_stable_digest(volume_source),
            "fcf":_stable_digest(fcf_effective) if fcf_accepted else None,
            "industry_company_profile":_stable_digest(industry_source)
                if industry_ref_status=="verified_current_company_classification_only"
                else None,
        },
        "notice":"Investor IAS is replaced by observed price-volume/OBV candidates up to 16/20, not identified net buying. Unobserved 4 volume points remain missing. No sector15, corporate-action PIT or certified trading score."
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
    replay_path=d/"v3-current-price-technical-momentum.json"
    replay=read(replay_path.name) if replay_path.is_file() else None
    rows,summary=generate(market,catalog,history,parts,weekly,cash,fcf,
                          industry,replay)
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
