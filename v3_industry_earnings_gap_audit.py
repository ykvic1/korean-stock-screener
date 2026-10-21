"""Fail-closed coverage diagnostics for *saved*, current-company industry earnings.

Use real DART company.json induty_code and saved FY2024/25 annual BS/IS.
This is a source-work queue: its reason labels mean missing/unusable IN OUR
SAVED VERIFICATION PIPELINE, not missing company filings or weak investments.
NO historical KRX sector constituent PIT, sector/15 scores, investor-flow
signals, newly fetched API data, individual operating income, or fiscal receipt
IDs are published.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
from io import StringIO
import json
from pathlib import Path

from v3_dart_rolling_book_reference import _inputs
from v3_dart_industry_codes import prior_records
from v3_dart_bulk_history import _day,_stable_digest,_valid_receipt, _validate_prior,write_json
from v3_dart_industry_earnings_sample import (
    IndustryEarningsError,build as earnings_build,source_join_ready,_pair
)

SCHEMA="v3-industry-saved-annual-earnings-missingness-0.1"
CATEGORIES=(
    "saved_company_annual_history_absent",
    "saved_both_comparison_years_absent",
    "saved_prior_year_absent",
    "saved_latest_year_absent",
    "shared_CFS_OFS_pair_absent",
    "annual_account_ambiguity",
    "annual_receipt_not_verified",
    "annual_operating_income_missing",
    "annual_revenue_missing_or_nonpositive",
    "annual_amount_outside_safeguards",
    "unusable_saved_annual_detail",
)
DESCRIPTION={
 "saved_company_annual_history_absent":"해당 기업의 연간 원장이 현재 저장소에 없음",
 "saved_both_comparison_years_absent":"두 비교 사업연도의 원장이 모두 미확보",
 "saved_prior_year_absent":"2024년 비교 원장 미확보",
 "saved_latest_year_absent":"2025년 비교 원장 미확보",
 "shared_CFS_OFS_pair_absent":"두 연도에 공통 연결/별도 재무제표 범위 없음",
 "annual_account_ambiguity":"영업이익·매출액 중복 계정이 있어 자동확정 불가",
 "annual_receipt_not_verified":"저장된 연간보고 접수번호 검증 불가",
 "annual_operating_income_missing":"비교가능 영업이익 계정 금액 미확보",
 "annual_revenue_missing_or_nonpositive":"매출 계정 금액 누락 또는 0 이하",
 "annual_amount_outside_safeguards":"연간 금액이 허용된 검증 범위 밖",
 "unusable_saved_annual_detail":"정규화된 재무 원장 구조 또는 값이 불완전",
}

class IndustryGapError(ValueError):
    """Failure code without private account values or issuer names."""


def missing_reason(record, prior_year, latest_year, asof):
    """Exclusive, deterministic FIRST blocking cause, not a full causal proof."""
    if _pair(record,prior_year,latest_year,asof) is not None:
        return None
    if not isinstance(record,dict) or not isinstance(record.get("years"),dict):
        return CATEGORIES[0]
    years=record["years"]
    old=years.get(str(prior_year))
    new=years.get(str(latest_year))
    old_ok=isinstance(old,dict) and bool(old)
    new_ok=isinstance(new,dict) and bool(new)
    if not old_ok and not new_ok:
        return CATEGORIES[1]
    if not old_ok:
        return CATEGORIES[2]
    if not new_ok:
        return CATEGORIES[3]
    matched=[scope for scope in ("CFS","OFS")
             if isinstance(old.get(scope),dict)
             and isinstance(new.get(scope),dict)
             and old[scope] and new[scope]]
    if not matched:
        return CATEGORIES[4]
    candidates=[]
    for scope in matched:
        defects=[]
        for year,data in ((prior_year,old[scope]),(latest_year,new[scope])):
            if not isinstance(data.get("values"),dict):
                defects.append("unusable_saved_annual_detail")
                continue
            vals=data["values"]
            op=vals.get("operating_income")
            rev=vals.get("revenue")
            ambiguous=data.get("ambiguous")
            if not isinstance(ambiguous,list):
                defects.append("unusable_saved_annual_detail")
            elif ("operating_income" in ambiguous or "revenue" in ambiguous):
                defects.append("annual_account_ambiguity")
            if not _valid_receipt(data.get("receipt_id"),year,_day(asof)):
                defects.append("annual_receipt_not_verified")
            if type(op) is not int:
                defects.append("annual_operating_income_missing")
            elif abs(op)>10**18:
                defects.append("annual_amount_outside_safeguards")
            if type(rev) is not int or rev<=0:
                defects.append("annual_revenue_missing_or_nonpositive")
        candidates.append((len(defects),0 if scope=="CFS" else 1,defects))
    _,_,faults=min(candidates,key=lambda entry:(entry[0],entry[1]))
    if not faults:
        raise IndustryGapError("paired_earnings_inconsistent_with_saved_source")
    # Prefer clearly fixable source quality flags when multiple issues coexist.
    ordered=tuple(k for k in CATEGORIES if k in faults)
    return ordered[0]


def build(market,catalog,industry,bulk):
    """Audit exact same-day source state and reconcile to original 2Y sample."""
    if not source_join_ready(market,catalog,industry,bulk):
        raise IndustryGapError("industry_earnings_sources_not_same_market_date")
    try:
        asof,members=_inputs(market,catalog)
        profiles=prior_records(industry,asof,members)
        saved=_validate_prior(bulk,asof,members)
        sample=earnings_build(market,catalog,industry,bulk)
    except (ValueError,KeyError,TypeError,IndustryEarningsError):
        raise IndustryGapError("untrusted_real_industry_filing_source") from None
    previous,latest=sample["fiscal_year_pair"]
    missing=[]
    categories=Counter()
    by_market=defaultdict(Counter)
    by_group=defaultdict(lambda:{"classified":0,"paired":0,"missing":Counter()})
    matched=0
    if industry["observed_industry_codes"]!=len(members):
        raise IndustryGapError("market_industry_codes_not_fully_observed")
    for code in sorted(members):
        src=profiles.get(code)
        if not isinstance(src,dict) or not isinstance(src.get("industry_code"),str):
            raise IndustryGapError("unclassified_current_company_code")
        market_name=members[code]["market"]
        prefix=src["industry_code"][:2]
        group=by_group[(market_name,prefix)]
        group["classified"]+=1
        reason=missing_reason(saved.get(code),previous,latest,asof)
        if reason is None:
            matched+=1
            group["paired"]+=1
            continue
        categories[reason]+=1
        by_market[market_name][reason]+=1
        group["missing"][reason]+=1
        missing.append({
          "code":code,"name":members[code]["name"],"market":market_name,
          "industry_prefix_2":prefix,"saved_source_gap":reason,
        })
    if (len(missing)+matched!=len(members) or
        matched!=sample["mapped_industry_issuer_earnings_two_year_matched"] or
        sample["dart_current_industry_codes_observed"]!=len(members) or
        len(by_group)!=sample["observed_2digit_code_market_groups"]):
        raise IndustryGapError("saved_annual_gap_does_not_reconcile_to_sample")
    sample_groups={(g["market"],g["dart_industry_code_prefix_2"]):g
                   for g in sample["groups"]}
    group_rows=[]
    for (market_name,prefix),v in sorted(by_group.items()):
        src=sample_groups.get((market_name,prefix))
        if (not isinstance(src,dict) or
            v["classified"]!=src["current_classified_issuers"] or
            v["paired"]!=src["issuer_year_pair_observed"] or
            v["classified"]-v["paired"]!=sum(v["missing"].values())):
            raise IndustryGapError("industry_sample_group_counts_disagree")
        group_rows.append({
          "market":market_name,"industry_code_prefix_2":prefix,
          "classified":v["classified"],"compared":v["paired"],
          "missing":v["classified"]-v["paired"],
          "source_coverage_ratio":round(v["paired"]/v["classified"],6),
          "gap_reason_counts":{k:v["missing"][k] for k in CATEGORIES if v["missing"][k]},
          "profit_scope_uniform_in_observed_sample":src["observed_pair_scope_uniform"],
          "source_is_current_dart_company_industry_not_krx_sector":True,
        })
    stats={
      "schema":SCHEMA,"asof":asof,
      "comparison_fiscal_years":[previous,latest],
      "listed_total":len(members),
      "current_company_industry_codes_observed":len(profiles),
      "actual_saved_annual_profit_pairs":matched,
      "saved_profit_pair_unavailable":len(missing),
      "missing_reason_counts":{k:categories[k] for k in CATEGORIES},
      "missing_reason_display_ko":dict(DESCRIPTION),
      "missing_by_market":{
        m:{k:by_market[m][k] for k in CATEGORIES}
        for m in ("KOSPI","KOSDAQ")},
      "group_count":len(group_rows),"groups":group_rows,
      "per_issuer_gap_queue_rows":len(missing),
      "priority_queue_from_stored_data_only":True,
      "official_krx_sector_membership_pit_verified":False,
      "actual_industry_15_points_authorized":False,
      "six_pillar_buy_recommendations_certified":False,
      "full_market_profit_sector_score_certified":False,
      "source_calls":0,
      "source_sha256":{
        "market_catalog":_stable_digest(catalog),
        "industry_current_company_codes":_stable_digest(industry),
        "dart_annual_receipt_history":_stable_digest(bulk),
      },
      "note":"Gap labels describe THIS saved annual-receipt pipeline, not an official statement that filings do not exist. Two-digit DART code prefix is not an authenticated historical KRX sector, no industry/15 points.",
    }
    return stats,missing


def missing_csv(rows,stats):
    stream=StringIO(newline="")
    w=csv.writer(stream,lineterminator="\n")
    w.writerow(["종목코드","종목명","시장","현재DART업종코드앞2자리",
                "공시자료기준일","저장원장_첫차단원인","원인설명",
                "과거KRX업종확인","산업15점투자점수"])
    for row in rows:
        name=row["saved_source_gap"]
        if name not in DESCRIPTION:
            raise IndustryGapError("unknown_industry_gap_reason")
        w.writerow([row["code"],row["name"],row["market"],
                    row["industry_prefix_2"],stats["asof"],name,
                    DESCRIPTION[name],"미확인",""])
    if len(rows)!=stats["saved_profit_pair_unavailable"]:
        raise IndustryGapError("industry_gap_csv_count_mismatch")
    return "\ufeff"+stream.getvalue()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs",default="docs")
    parser.add_argument("--mode",choices=("write-if-ready","verify-if-ready","write","verify"),
                        default="write-if-ready")
    args=parser.parse_args(argv)
    root=Path(args.docs)
    def read(name):
        return json.loads((root/name).read_text(encoding="utf8"))
    market=read("data.json")
    catalog=read("v22-market-catalog.json")
    industry=read("v3-dart-industry-classification.json")
    bulk=read("v3-dart-bulk-history.json")
    if args.mode.endswith("-if-ready") and not source_join_ready(
         market,catalog,industry,bulk):
        print(json.dumps({"status":"deferred_same_day_industry_fiscal_join",
                          "market_asof":catalog["date"],
                          "saved_major_asof":bulk["asof"],"api_calls":0},
                         sort_keys=True))
        return
    stats,missing=build(market,catalog,industry,bulk)
    data=root/"v3-industry-saved-profit-gap-summary.json"
    sheet=root/"v3-industry-saved-profit-gap-issuers.csv"
    rendered=missing_csv(missing,stats)
    if args.mode.startswith("write"):
        write_json(data,stats)
        sheet.write_text(rendered,encoding="utf8")
    else:
        if (not data.is_file() or not sheet.is_file()
            or read(data.name)!=stats or sheet.read_text(encoding="utf8")!=rendered):
            raise IndustryGapError("industry_gap_source_changed_after_rebase")
    print(json.dumps({"status":"saved_industry_profit_missingness_audited",
                      "asof":stats["asof"],"listed":stats["listed_total"],
                      "profit_pairs":stats["actual_saved_annual_profit_pairs"],
                      "not_yet_usable":stats["saved_profit_pair_unavailable"],
                      "cause_counts":stats["missing_reason_counts"],
                      "official_industry_score_count":0,"api_calls":0},
                     sort_keys=True))


if __name__=="__main__":
    main()
