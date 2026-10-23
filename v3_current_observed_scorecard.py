"""Same-close all-listed provisional scorecard, never blocked by older DART.

Only independent current-close price evidence is always replayed:
volume/OBV <=16/20, technical <=25/25, momentum <=8/10.
When independently verified current-close DART filing evidence EXISTS, its
derived financial <=8/15 and simple valuation <=10/15 are also displayed.
Independently saved, receipt-matched FY2023-25 DART CF/CAPEX can contribute an
additional financial <=4 and valuation <=5 when all same-day source joins pass.
Stale, conflicting or uncertified filings NEVER become new-day points. Industry
remains EMPTY; no 100-point rescaling or official investment certification.
"""
from __future__ import annotations
import argparse
import csv
from io import StringIO
import json
from pathlib import Path

from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build
from v3_all_listed_scorecard import _momentum, _contribution
from v3_weekly_rotation import technical_points
from v3_dart_bulk_history import _stable_digest, research_parts, BulkHistoryError
from v3_real_six_pillar_coverage_audit import _canonical_hash
from v3_dart_cashflow_factor_preview import score_preview, FCFPreviewError
from v3_dart_cashflow_enrichment import CashflowError, fingerprint

SCHEMA="v3-current-day-all-listed-observed-plus-dart-fcf-0.3"
POINT_BUDGET={"volume":20,"technical":25,"financial":20,
              "valuation":20,"momentum":15}

class LivePartialScorecardError(ValueError):
    """Never include raw provider data in failures."""


def finance_source(catalog,history,parts,annual,audit):
    """Use ONLY the already-public audited DART files from this market close.

    If the yearly collector is behind today's close, leave its weights blank;
    never copy yesterday's 4-year financial rank to today's price row.
    Recompute from real stored fiscal receipts rather than trust a score CSV.
    """
    today=catalog["date"]
    docs=(history,parts,annual,audit)
    if any(type(x) is not dict for x in docs):
        return None
    days=(history.get("asof"),parts.get("asof"),
          annual.get("asof"),audit.get("market_asof"))
    if any(day!=today for day in days):
        return None
    if (history.get("historical_PIT_source_certified") is not False or
        history.get("raw_source_rows_published") is not False or
        parts.get("historical_PIT_certified") is not False or
        parts.get("all_scores_provisional_unverified") is not True or
        parts.get("fully_verified_six_pillar_count")!=0 or
        annual.get("official_exchange_pbr_verified") is not False or
        annual.get("historical_point_in_time_verified") is not False or
        audit.get("six_pillar_official_complete_100pt_issuer_count")!=0 or
        audit.get("investment_grade_scores_confirmed") is not False or
        audit.get("official_pbr_verified_count")!=0):
        raise LivePartialScorecardError("forged_dart_source_or_certification")
    hashrefs=audit.get("source_sha256_canonical") or {}
    if (hashrefs.get("market_catalog")!=_canonical_hash(catalog) or
        hashrefs.get("dart_four_year_archive")!=_canonical_hash(history) or
        hashrefs.get("dart_partial_score")!=_canonical_hash(parts)):
        return None   # collector changed before coverage report was re-audited
    try:
        expected=research_parts(history,catalog,annual)
    except (BulkHistoryError,ValueError,TypeError,KeyError):
        return None
    if expected!=parts:
        return None   # receipt/annual book rebase changed; resync first
    rows={r["code"]:r for r in parts["rows"]}
    if len(rows)!=len(parts["rows"]):
        raise LivePartialScorecardError("duplicate_dart_financial_issuer")
    area=audit.get("six_pillar_numeric_provisional_issuer_counts") or {}
    actual_fin=sum(x.get("financial_available_max",0)>0 for x in rows.values())
    actual_val=sum(x.get("valuation_available_max",0)>0 for x in rows.values())
    if (area.get("financial")!=actual_fin or
        area.get("valuation")!=actual_val or
        audit.get("listed_total")!=catalog["listed_total"]):
        return None
    return rows


def cashflow_source(market,catalog,history,cash,stored_preview):
    """Recompute only true same-company, same-scope, same-receipt DART FCF.

    A saved preview from before later legitimate major-account additions may
    be stale; only its SOURCE fingerprints may differ. A changed row with the
    exact same source fingerprints is an invalid claim, never accepted.
    """
    if type(cash) is not dict or type(stored_preview) is not dict:
        return None
    day=catalog["date"]
    if (cash.get("asof")!=day or stored_preview.get("asof")!=day
        or history.get("asof")!=day):
        return None
    if (cash.get("historical_PIT_certified") is not False or
        cash.get("raw_dart_rows_published") is not False or
        cash.get("official_six_pillar_100pt_certified") is not False or
        cash.get("three_year_fcf_financial15_certified") is not False or
        cash.get("current_year_fcf_valuation15_certified") is not False or
        cash.get("cashflow_accounting_consolidated_with_major_source") is not False or
        stored_preview.get("hist_PIT_verified") is not False or
        stored_preview.get("six_pillar_confirmed_100pt") is not None or
        stored_preview.get("raw_source_rows_stored") is not False or
        stored_preview.get("source_same_filing_scope_receipt_checked") is not True or
        stored_preview.get("buy_recommendations_authorized") is not False or
        stored_preview.get("joined_to_main_v3_ranking") is not False or
        stored_preview.get("four_year_financial15_complete") is not False or
        stored_preview.get("valuation15_complete") is not False):
        raise LivePartialScorecardError("untrusted_fcf_source_claim")
    try:
        expected=score_preview(market,catalog,history,cash,minimum_peers=30)
    except (CashflowError,FCFPreviewError,ValueError,KeyError,TypeError):
        return None
    if (stored_preview.get("market_catalog_sha256")==fingerprint(catalog) and
        stored_preview.get("cashflow_snapshot_sha256")==fingerprint(cash) and
        stored_preview.get("major_history_sha256")==fingerprint(history) and
        stored_preview!=expected):
        raise LivePartialScorecardError("matched_fcf_preview_rows_changed_without_source")
    rows={r["code"]:r for r in expected["rows"]}
    if len(rows)!=len(expected["rows"]):
        raise LivePartialScorecardError("duplicate_fcf_source_issuer")
    return rows,expected


def generate(market,catalog,volume,price,financial=None,cashflow=None):
    """Compute all current listed tickers; verify EVERY reused source row."""
    true_volume=volume_build(market,catalog)
    true_price=price_build(market,catalog)
    if volume!=true_volume or price!=true_price:
        raise LivePartialScorecardError("not_exact_same_day_source_replay")
    asof=catalog["date"]
    if asof!=market["date"] or volume["asof"]!=asof or price["asof"]!=asof:
        raise LivePartialScorecardError("different_market_date")
    observed={r["code"]:r for r in market["stocks"]}
    output=[]
    counts={"volume":0,"technical":0,"momentum":0,
            "financial":0,"valuation":0}
    fcf_counts={"financial4":0,"valuation5":0}
    nonblank=0
    five_area_overlap=0
    available_weight_distribution={}
    available_weight_by_market={}
    five_area_overlap_by_market={}
    for listed in sorted(catalog["rows"],key=lambda r:r["code"]):
        code=listed["code"]
        v=volume["records"].get(code)
        p=price["rows"].get(code)
        values={"volume":None,"technical":None,"financial":None,
                "valuation":None,"industry":None,"momentum":None}
        coverage={"volume":0,"technical":0,"financial":0,
                  "valuation":0,"industry":0,"momentum":0}
        if v is not None:
            values["volume"],coverage["volume"]=_contribution(
                v["observed_points"],v["observed_available_max"],20)
        if p is not None:
            if (p["market"]!=listed["market"] or p["asof"]!=asof or
                p["official_investment_score_certified"] is not False):
                raise LivePartialScorecardError("invalid_market_price_replay_identity")
            t=p["technical_research_reference"]
            if t is not None:
                if code not in observed:
                    raise LivePartialScorecardError("unpriced_technical_candidate")
                points,available,_=technical_points(observed[code],asof)
                if (not available or points is None or
                    t.get("technical_observed_maximum")!=available):
                    raise LivePartialScorecardError("inconsistent_replayed_technical")
                values["technical"],coverage["technical"]=_contribution(
                    points,available,25)
            mom=p["momentum_research_reference"]
            if mom is not None:
                wrapper={"momentum_research_reference":mom,
                         "momentum_available_weight":mom["momentum_observed_maximum"],
                         "momentum_research_points":mom["momentum_research_candidate"]}
                values["momentum"],coverage["momentum"]=_momentum(
                    wrapper,asof,listed["market"])
                if coverage["momentum"]==0:
                    raise LivePartialScorecardError("invalid_replayed_momentum")
        if financial is not None and code in financial:
            fiscal=financial[code]
            if (fiscal.get("code")!=code or
                fiscal.get("market")!=listed["market"] or
                fiscal.get("asof")!=asof or
                fiscal.get("confirmed_v3_score") is not None or
                fiscal.get("scoring_certified") is not False):
                raise LivePartialScorecardError("fiscal_price_join_identity_invalid")
            for group in ("financial","valuation"):
                points=fiscal.get(group+"_candidate_points")
                available=fiscal.get(group+"_available_max")
                values[group],coverage[group]=_contribution(
                    points,available,POINT_BUDGET[group])
        fcf_earned={"financial4":None,"valuation5":None}
        if cashflow is not None and code in cashflow:
            entry=cashflow[code]
            if (entry.get("code")!=code or entry.get("market")!=listed["market"] or
                entry.get("asof")!=asof or
                entry.get("strict_receipt_scope_matched") is not True or
                entry.get("historical_PIT_certified") is not False or
                entry.get("scoring_certified") is not False):
                raise LivePartialScorecardError("fcf_join_issuer_or_claim_mismatch")
            for group,field,maximum,key in (
                ("financial","financial_fcf_assets",4,"financial4"),
                ("valuation","valuation_fcf_yield",5,"valuation5")):
                extra=entry.get(field+"_candidate_points")
                weight=entry.get(field+"_available_max")
                if (type(weight) is not int or weight not in (0,maximum)
                    or (weight==0 and extra is not None)
                    or (weight==maximum and
                        (type(extra) not in (float,int) or
                         not 0<=extra<=maximum))):
                    raise LivePartialScorecardError("invented_or_invalid_fcf_component")
                if weight:
                    base,base_weight=values[group],coverage[group]
                    if base_weight+weight>POINT_BUDGET[group]:
                        raise LivePartialScorecardError("fcf_double_count_or_weight_overflow")
                    values[group],coverage[group]=_contribution(
                        (base or 0)+extra,base_weight+weight,POINT_BUDGET[group])
                    fcf_earned[key]=extra
                    fcf_counts[key]+=1
        for area in counts:
            if coverage[area]>0:
                counts[area]+=1
        available=sum(coverage.values())
        earned=sum(v for v in values.values() if v is not None)
        if not 0<=earned<=available<=sum(POINT_BUDGET.values()):
            raise LivePartialScorecardError("invented_current_day_score")
        if available:
            nonblank+=1
        stock_market=listed["market"]
        market_weights=available_weight_by_market.setdefault(stock_market,{})
        market_weights[str(available)]=market_weights.get(str(available),0)+1
        if all(coverage[area]>0 for area in POINT_BUDGET):
            five_area_overlap+=1
            five_area_overlap_by_market[stock_market]=five_area_overlap_by_market.get(stock_market,0)+1
        available_weight_distribution[str(available)]=available_weight_distribution.get(str(available),0)+1
        output.append({
            "code":code,"name":listed["name"],"market":listed["market"],
            "asof":asof,"earned":round(earned,4) if available else None,
            "available":available,"unavailable":100-available,
            "factors":values,"factor_available":coverage,
            "fcf_subpoints":fcf_earned,
            "official_score":None,"certified":False,
        })
    if (len(output)!=catalog["listed_total"]
        or counts["volume"]!=volume["observed_volume_candidate_issuers"]
        or counts["technical"]!=price["technical_research_issuer_count"]
        or counts["momentum"]!=price["momentum_research_issuer_count"]):
        raise LivePartialScorecardError("source_counts_do_not_reconcile")
    observed_area_max={area:max(r["factor_available"][area] for r in output)
                       for area in POINT_BUDGET}
    missing_area_weight={area:POINT_BUDGET[area]-observed_area_max[area]
                         for area in POINT_BUDGET}
    summary={
        "schema":SCHEMA,"asof":asof,"listed_total":len(output),
        "observed_any_factor_issuers":nonblank,
        "five_area_observed_overlap_issuers":five_area_overlap,
        "available_weight_distribution":available_weight_distribution,
        "available_weight_distribution_by_market":available_weight_by_market,
        "five_area_observed_overlap_by_market":five_area_overlap_by_market,
        "observed_issuer_counts":{**counts,"industry":0},
        "area_budgets":dict(POINT_BUDGET),
        "factor_observed_weight_ceiling":observed_area_max,
        "factor_unobserved_weight_gap":missing_area_weight,
        "total_unobserved_weight_gap":sum(missing_area_weight.values()),
        "highest_currently_observable_weight":sum(observed_area_max.values()),
        "max_single_issuer_available_weight":max(r["available"] for r in output),
        "fcf_observed_issuer_counts":fcf_counts,
        "fcf_reference_join_status":(
            "same_fiscal_year_scope_receipt_replayed" if cashflow is not None
            else "unavailable_or_not_same_source_day"),
        "fiscal_reference_join_status":(
            "same_day_saved_receipts_replayed" if financial is not None
            else "not_ready_or_unverified_annual_source"),
        "official_investor_flows_inferred":False,
        "same_day_financial_and_sector_qualification":False,
        "missing_weight_is_not_zero":True,
        "not_an_investment_ranking":True,
        "certified_100point_count":0,
        "provider_requests":0,
        "source_sha256":{
            "market":_stable_digest(market),"catalog":_stable_digest(catalog),
            "volume":_stable_digest(volume),"price":_stable_digest(price),
        },
    }
    return output,summary


def to_csv(rows,summary):
    sink=StringIO(newline="")
    out=csv.writer(sink,lineterminator="\n")
    out.writerow(["종목코드","종목명","시장","가격자료기준일",
        "거래량_OBV_잠정점수","거래량_OBV_산출배점",
        "기술_잠정점수","기술_산출배점",
        "재무_잠정점수","밸류에이션_잠정점수","산업_잠정점수",
        "모멘텀_잠정점수","모멘텀_산출배점",
        "관측잠정합계_100점중","산출가능배점_100점중",
        "미확보배점_100점중","정식투자점수","투자등급",
        "재무_산출배점","가치_산출배점",
        "FCF자산3년_잠정점수","FCF수익률_잠정점수"])
    for row in rows:
        v=row["factors"]
        a=row["factor_available"]
        out.writerow([
            row["code"],row["name"],row["market"],row["asof"],
            "" if v["volume"] is None else v["volume"],a["volume"] or "",
            "" if v["technical"] is None else v["technical"],a["technical"] or "",
            "" if v["financial"] is None else v["financial"],
            "" if v["valuation"] is None else v["valuation"],"",
            "" if v["momentum"] is None else v["momentum"],a["momentum"] or "",
            "" if row["earned"] is None else row["earned"],
            row["available"] or "",row["unavailable"],"","미검증 연구용",
            a["financial"] or "",a["valuation"] or "",
            "" if row["fcf_subpoints"]["financial4"] is None
                else row["fcf_subpoints"]["financial4"],
            "" if row["fcf_subpoints"]["valuation5"] is None
                else row["fcf_subpoints"]["valuation5"]
        ])
    return "\ufeff"+sink.getvalue()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs",default="docs")
    parser.add_argument("--mode",choices=("write","verify"),default="write")
    args=parser.parse_args(argv)
    root=Path(args.docs)
    def read(name):
        return json.loads((root/name).read_text(encoding="utf8"))
    catalog=read("v22-market-catalog.json")
    try:
        financial=finance_source(
            catalog,read("v3-dart-bulk-history.json"),
            read("v3-dart-bulk-provisional.json"),
            read("v3-dart-book-reference.json"),
            read("v3-real-source-six-area-coverage.json"))
    except (OSError,ValueError,KeyError):
        financial=None
    market=read("data.json")
    cashflow=None
    if financial is not None:
        try:
            candidate=cashflow_source(
                market,catalog,read("v3-dart-bulk-history.json"),
                read("v3-dart-cashflow-reference.json"),
                read("v3-dart-fcf-research-preview.json"))
            if candidate is not None:
                cashflow,_=candidate
        except (OSError,ValueError,KeyError):
            cashflow=None
    rows,summary=generate(
        market,catalog,read("v3-volume-obv-observed.json"),
        read("v3-current-price-technical-momentum.json"),
        financial=financial,cashflow=cashflow)
    new_csv=to_csv(rows,summary)
    target=root/"v3-current-observed-all-listed.csv"
    status=root/"v3-current-observed-all-listed-summary.json"
    if args.mode=="write":
        target.write_text(new_csv,encoding="utf8")
        from v3_dart_bulk_history import write_json
        write_json(status,summary)
    else:
        if (not target.is_file() or not status.is_file()
            or target.read_text(encoding="utf8")!=new_csv
            or json.loads(status.read_text(encoding="utf8"))!=summary):
            raise LivePartialScorecardError("live_partial_publication_differs_from_sources")
    print(json.dumps({
        "status":"current_market_actual_observed_only",
        "asof":summary["asof"],
        "listed":summary["listed_total"],
        "observed":summary["observed_any_factor_issuers"],
        "by_area":summary["observed_issuer_counts"],
        "available_points_ceiling":summary["highest_currently_observable_weight"],
        "receipt_matched_fcf_issuer_counts":summary["fcf_observed_issuer_counts"],"certified_100point":0,
    },sort_keys=True))


if __name__=="__main__":
    main()
