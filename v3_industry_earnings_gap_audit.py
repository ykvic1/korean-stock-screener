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

SCHEMA="v3-industry-saved-annual-earnings-missingness-0.3-breadth-research"
MIN_BREADTH_SAMPLE=3
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


def verified_operating_income_pair_without_revenue(record, prior_year, latest_year, asof):
    """Proof of exact FY annual operating profit even when revenue is unusable.

    Uses one SAME CFS or OFS financial scope across two years and exact annual
    receipts. Missing/ambiguous operating profit is never treated as zero.
    Numeric profits MUST NEVER be serialized in the saved gap queue.
    """
    if type(record) is not dict or type(record.get("years")) is not dict:
        return None
    years=record["years"]
    for scope in ("CFS","OFS"):
        amounts=[]
        for year in (prior_year,latest_year):
            fiscal=years.get(str(year))
            if type(fiscal) is not dict:
                break
            data=fiscal.get(scope)
            if type(data) is not dict or type(data.get("values")) is not dict:
                break
            value=data["values"].get("operating_income")
            if (type(value) is not int or abs(value)>10**18
                or type(data.get("ambiguous")) is not list
                or "operating_income" in data["ambiguous"]
                or not _valid_receipt(data.get("receipt_id"),year,_day(asof))):
                break
            amounts.append(value)
        if len(amounts)==2:
            return scope,amounts[0],amounts[1]
    return None


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
    try:
        ready=source_join_ready(market,catalog,industry,bulk)
    except (IndustryEarningsError,ValueError,TypeError,KeyError):
        raise IndustryGapError("untrusted_industry_source_preflight") from None
    if not ready:
        raise IndustryGapError("industry_earnings_sources_not_same_market_date")
    try:
        asof,members=_inputs(market,catalog)
        profiles=prior_records(industry,asof,members)
        saved=_validate_prior(bulk,asof,members)
        sample=earnings_build(market,catalog,industry,bulk)
    except (ValueError,KeyError,TypeError,IndustryEarningsError):
        raise IndustryGapError("untrusted_real_industry_filing_source") from None
    previous,latest=sample["fiscal_year_pair"]
    titles={item["code"]:item["name"] for item in catalog["rows"]}
    if (len(titles)!=len(members) or set(titles)!=set(members)
        or any(not isinstance(title,str) or not title.strip()
               for title in titles.values())):
        raise IndustryGapError("listed_issuer_names_not_reconciled")
    missing=[]
    categories=Counter()
    by_market=defaultdict(Counter)
    profit_only_by_reason=Counter()
    profit_only_by_market=Counter()
    by_group=defaultdict(lambda:{"classified":0,"paired":0,"missing":Counter(),
                                  "verified_profit_only_missing_revenue":0,
                                  "verified_operating_profit":0,
                                  "operating_profit_improved":0})
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
            strict=_pair(saved.get(code),previous,latest,asof)
            if strict is None:
                raise IndustryGapError("strict_pair_not_reproducible")
            matched+=1
            group["paired"]+=1
            group["verified_operating_profit"]+=1
            group["operating_profit_improved"]+=int(strict[2]>strict[1])
            continue
        categories[reason]+=1
        by_market[market_name][reason]+=1
        group["missing"][reason]+=1
        profit=verified_operating_income_pair_without_revenue(
            saved.get(code),previous,latest,asof)
        # This is independent proof of ONLY the two operating profit amounts;
        # do not count as a regular pair, revenue, or any industry/15 points.
        profit_only=profit is not None
        if profit_only:
            profit_only_by_reason[reason]+=1
            profit_only_by_market[market_name]+=1
            group["verified_profit_only_missing_revenue"]+=1
            group["verified_operating_profit"]+=1
            group["operating_profit_improved"]+=int(profit[2]>profit[1])
        missing.append({
          "code":code,"name":titles[code],"market":market_name,
          "industry_prefix_2":prefix,"saved_source_gap":reason,
          "operating_profit_pair_without_revenue_verified":profit_only,
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
            v["classified"]-v["paired"]!=sum(v["missing"].values()) or
            v["verified_operating_profit"]!=
                v["paired"]+v["verified_profit_only_missing_revenue"] or
            not 0<=v["operating_profit_improved"]<=v["verified_operating_profit"]
                <=v["classified"]):
            raise IndustryGapError("industry_sample_group_counts_disagree")
        comparable=v["verified_operating_profit"]
        improved=v["operating_profit_improved"]
        current_total=v["classified"]
        publish_breadth=comparable>=MIN_BREADTH_SAMPLE
        group_rows.append({
          "market":market_name,"industry_code_prefix_2":prefix,
          "classified":v["classified"],"compared":v["paired"],
          "missing":v["classified"]-v["paired"],
          "source_coverage_ratio":round(v["paired"]/v["classified"],6),
          "gap_reason_counts":{k:v["missing"][k] for k in CATEGORIES if v["missing"][k]},
          "verified_profit_only_gap_count":
              v["verified_profit_only_missing_revenue"],
          # Source-backed FY changes by exact issuer and same CFS/OFS scope,
          # including profit-only companies WITHOUT inventing their revenue.
          # These are CURRENT company-code samples, never historical sectors.
          "two_year_operating_profit_verified_issuers":comparable,
          "two_year_operating_profit_improving_issuers":
              improved if publish_breadth else None,
          "two_year_profit_improving_ratio_observed_sample_only":
              round(improved/comparable,6) if publish_breadth else None,
          "current_code_improving_breadth_lower_bound":
              round(improved/current_total,6) if publish_breadth else None,
          "current_code_improving_breadth_upper_bound":
              round((improved+current_total-comparable)/current_total,6)
              if publish_breadth else None,
          "profit_scope_uniform_in_observed_sample":src["observed_pair_scope_uniform"],
          "source_is_current_dart_company_industry_not_krx_sector":True,
        })
    verified_total=matched+sum(profit_only_by_reason.values())
    if sum(g["two_year_operating_profit_verified_issuers"]
           for g in group_rows)!=verified_total:
        raise IndustryGapError("combined_profit_breadth_count_disagrees")
    stats={
      "schema":SCHEMA,"asof":asof,
      "comparison_fiscal_years":[previous,latest],
      "listed_total":len(members),
      "current_company_industry_codes_observed":len(profiles),
      "actual_saved_annual_profit_pairs":matched,
      "saved_profit_pair_unavailable":len(missing),
      "verified_operating_profit_only_pair_among_missing":sum(profit_only_by_reason.values()),
      "two_year_operating_profit_verified_any_revenue":verified_total,
      "two_year_operating_profit_coverage_any_revenue":
          round(verified_total/len(members),6),
      "two_year_profit_breadth_current_company_code_sample_only":True,
      "historical_sector_profit_breadth_certified":False,
      "profit_only_verified_but_full_pair_still_missing":True,
      "profit_only_among_missing_by_reason":{k:profit_only_by_reason[k] for k in CATEGORIES},
      "profit_only_among_missing_by_market":{
         m:profit_only_by_market[m] for m in ("KOSPI","KOSDAQ")},
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
      "note":"Gap labels describe THIS saved annual-receipt pipeline, not an official statement that filings do not exist. Verified profit-only pairs permit an independent FY2024/25 operating-profit improvement sample, but do NOT repair missing revenues, update the strict paired count, or authenticate historical KRX sector memberships. Current-code breadth bounds and ratios are NOT live sector/15 points.",
    }
    return stats,missing


def industry_breadth_csv(stats):
    """Export grouped, observed FY2024/25 breadth without fictional sector points.

    Current-company DART industry codes are NOT historical KRX constituents.
    Groups with fewer than three comparable issuer profit pairs remain blank.
    Bounds show what missing CURRENT constituents could change, not certainty.
    """
    if (stats.get("actual_industry_15_points_authorized") is not False
        or stats.get("historical_sector_profit_breadth_certified") is not False
        or stats.get("official_krx_sector_membership_pit_verified") is not False
        or stats.get("full_market_profit_sector_score_certified") is not False
        or stats.get("source_calls")!=0
        or stats.get("comparison_fiscal_years")!=[2024,2025]
        or type(stats.get("groups")) is not list
        or len(stats["groups"])!=stats.get("group_count")
        or type(stats.get("asof")) is not str):
        raise IndustryGapError("untrusted_observed_industry_breadth_source")
    output=StringIO(newline="")
    writer=csv.writer(output,lineterminator="\\n")
    writer.writerow([
       "시장","현재DART업종코드앞2자리","현재상장기업수",
       "영업이익2개년_검증기업수","매출포함엄격비교기업수",
       "영업이익개선기업수_표본3개이상",
       "검증표본내_실적개선율_%","현재분류전체_개선율하한_%",
       "현재분류전체_개선율상한_%","영업이익비교_실측확보율_%",
       "공시자료기준일","과거KRX업종구성_PIT","공식산업15점",
    ])
    sums={"classified":0,"verified":0,"strict":0}
    seen=set()
    def pct(value):
        return f"{100*value:.2f}"
    for group in stats["groups"]:
        market=group.get("market")
        code=group.get("industry_code_prefix_2")
        if (market not in ("KOSPI","KOSDAQ")
            or type(code) is not str or len(code)!=2 or not code.isascii()
            or not code.isdigit() or (market,code) in seen
            or group.get("source_is_current_dart_company_industry_not_krx_sector") is not True):
            raise IndustryGapError("untrusted_current_company_industry_group")
        seen.add((market,code))
        total=group.get("classified")
        verified=group.get("two_year_operating_profit_verified_issuers")
        strict=group.get("compared")
        extra=group.get("verified_profit_only_gap_count")
        if (any(type(v) is not int for v in (total,verified,strict,extra))
            or not 0<=strict<=verified<=total or total<1
            or verified!=strict+extra):
            raise IndustryGapError("invalid_group_annual_profit_coverage")
        sums["classified"]+=total
        sums["verified"]+=verified
        sums["strict"]+=strict
        improved=group.get("two_year_operating_profit_improving_issuers")
        ratio=group.get("two_year_profit_improving_ratio_observed_sample_only")
        lower=group.get("current_code_improving_breadth_lower_bound")
        upper=group.get("current_code_improving_breadth_upper_bound")
        if verified<MIN_BREADTH_SAMPLE:
            if any(v is not None for v in (improved,ratio,lower,upper)):
                raise IndustryGapError("small_group_profit_breadth_disclosure")
            visible=["","","",""]
        else:
            if (type(improved) is not int or not 0<=improved<=verified
                or any(type(v) not in (int,float) or not 0<=v<=1
                       for v in (ratio,lower,upper))
                or abs(ratio-round(improved/verified,6))>0.000002
                or abs(lower-round(improved/total,6))>0.000002
                or abs(upper-round((improved+total-verified)/total,6))>0.000002
                or lower>ratio+0.000002 or upper+0.000002<ratio):
                raise IndustryGapError("invalid_observed_breadth_uncertainty")
            visible=[improved,pct(ratio),pct(lower),pct(upper)]
        writer.writerow([
            market,code,total,verified,strict,*visible,
            pct(verified/total),stats["asof"],"미확인","",
        ])
    if (sums["classified"]!=stats.get("listed_total")
        or sums["verified"]!=stats.get("two_year_operating_profit_verified_any_revenue")
        or sums["strict"]!=stats.get("actual_saved_annual_profit_pairs")):
        raise IndustryGapError("industry_group_breadth_totals_mismatch")
    return "\\ufeff"+output.getvalue()


def missing_csv(rows,stats):
    stream=StringIO(newline="")
    w=csv.writer(stream,lineterminator="\n")
    w.writerow(["종목코드","종목명","시장","현재DART업종코드앞2자리",
                "공시자료기준일","저장원장_첫차단원인","원인설명",
                "영업이익_2개년_매출무관_별도검증","매출포함_엄격비교",
                "과거KRX업종확인","산업15점투자점수"])
    for row in rows:
        name=row["saved_source_gap"]
        if name not in DESCRIPTION:
            raise IndustryGapError("unknown_industry_gap_reason")
        proven=row.get("operating_profit_pair_without_revenue_verified")
        if type(proven) is not bool:
            raise IndustryGapError("unverified_profit_only_gap_queue")
        w.writerow([row["code"],row["name"],row["market"],
                    row["industry_prefix_2"],stats["asof"],name,
                    DESCRIPTION[name],"검증" if proven else "미검증",
                    "미확보","미확인",""])
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
    breadth=root/"v3-industry-observed-profit-breadth.csv"
    rendered=missing_csv(missing,stats)
    breadth_rendered=industry_breadth_csv(stats)
    if args.mode.startswith("write"):
        write_json(data,stats)
        sheet.write_text(rendered,encoding="utf8")
        breadth.write_text(breadth_rendered,encoding="utf8")
    else:
        if (not data.is_file() or not sheet.is_file() or not breadth.is_file()
            or read(data.name)!=stats or sheet.read_text(encoding="utf8")!=rendered
            or breadth.read_text(encoding="utf8")!=breadth_rendered):
            raise IndustryGapError("industry_gap_source_changed_after_rebase")
    print(json.dumps({"status":"saved_industry_profit_missingness_audited",
                      "asof":stats["asof"],"listed":stats["listed_total"],
                      "profit_pairs":stats["actual_saved_annual_profit_pairs"],
                      "not_yet_usable":stats["saved_profit_pair_unavailable"],
                      "profit_only_pairs_even_without_revenue":
                        stats["verified_operating_profit_only_pair_among_missing"],
                      "cause_counts":stats["missing_reason_counts"],
                      "official_industry_score_count":0,"api_calls":0},
                     sort_keys=True))


if __name__=="__main__":
    main()
