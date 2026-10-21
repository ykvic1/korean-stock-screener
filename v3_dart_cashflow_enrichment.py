"""Bounded official DART CASH FLOW / CAPEX research evidence (NOT a V3 score).

fnlttMultiAcnt provides BS/IS. This independently probes annual CF rows
through fnlttSinglAcntAll, using only exact known IFRS account IDs.
All source account rows and credentials remain ephemeral. No missing CAPEX is
assumed zero; no cross-filing fiscal/issuer mix, PIT or investment certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from v3_dart_bulk_history import (
    _day, _amount, _inputs, _mapping, _stable_digest, _valid_receipt,
    verified_company_mapping_cache, make_company_mapping_cache, write_json,
)

SCHEMA="v3-observed-annual-dart-cashflow-capex-0.1"
MAX_ISSUERS=32
MAX_SOURCE_CALLS=96
MAX_SECONDS=950
YEARS=(2023,2024,2025)
CFO_IDS={
    "ifrs-full_CashFlowsFromUsedInOperatingActivities",
    "ifrs_CashFlowsFromUsedInOperatingActivities",
}
PPE_IDS={
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
    "ifrs_PurchaseOfPropertyPlantAndEquipment",
}
INTANGIBLE_IDS={
    "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
    "ifrs-full_PurchaseOfIntangibleAssets",
    "ifrs_PurchaseOfIntangibleAssets",
}

class CashflowError(ValueError):
    """Fixed, non-sensitive failure codes."""


def fingerprint(value):
    return _stable_digest(value)


def asof_members(market,catalog,bulk):
    asof,members=_inputs(market,catalog)
    # Never reuse FY2023-25 when FY2026 becomes the latest available annual
    # cohort; explicitly update the capture contract at fiscal rollover.
    if tuple(range(_day(asof).year-3,_day(asof).year))!=YEARS:
        raise CashflowError("cashflow_fiscal_rollover_requires_review")
    if (type(bulk) is not dict or bulk.get("asof")!=asof
        or bulk.get("listed_total")!=len(members)
        or bulk.get("fiscal_years")!=list(range(_day(asof).year-4,_day(asof).year))
        or bulk.get("historical_PIT_source_certified") is not False
        or type(bulk.get("records")) is not dict):
        raise CashflowError("incompatible_major_annual_source")
    return asof,members


def matched_scope(bulk_record):
    """Only all-three-year company/accounting-scope matched BS/IS origins."""
    years=bulk_record.get("years",{}) if type(bulk_record) is dict else {}
    for scope in ("CFS","OFS"):
        ok=True
        for year in YEARS:
            stmt=years.get(str(year),{}).get(scope,{})
            val=stmt.get("values",{})
            if (type(val.get("revenue")) is not int or val["revenue"]<=0
                or type(val.get("operating_income")) is not int
                or not isinstance(stmt.get("receipt_id"),str)):
                ok=False
                break
        if ok:
            return scope
    return None


def retained_current_market_cashflow(market,catalog,bulk,prior):
    """Carry genuine FY receipts to the next trading-day market snapshot.

    Only current-listed issuers, same fiscal window and unchanged exact
    major-account receipts survive. No provider call or inferred zero.
    """
    asof,members=asof_members(market,catalog,bulk)
    if prior is None:
        return None
    if type(prior) is not dict:
        raise CashflowError("untrusted_previous_cashflow_source")
    previous=prior.get("asof")
    try:
        previous_day=_day(previous)
        current_day=_day(asof)
    except (ValueError,TypeError):
        raise CashflowError("untrusted_previous_cashflow_date") from None
    if previous_day==current_day:
        verify_state(prior,market,catalog,bulk,strict_fingerprint=False)
        return prior
    if (previous_day>current_day or previous_day.year!=current_day.year
        or (current_day-previous_day).days>45
        or type(prior.get("listed_total")) is not int
        or not 1<=prior["listed_total"]<=6000
        or type(prior.get("records")) is not dict
        or len(prior["records"])>prior["listed_total"]):
        raise CashflowError("unsafe_cashflow_market_day_rollover")
    old_records=prior["records"]
    if any(type(c) is not str or not re.fullmatch(r"[0-9]{6}",c)
           for c in old_records):
        raise CashflowError("invalid_prior_company_identity")
    # Revalidate the old immutable snapshot independently of today's
    # membership without fabricating yesterday's entire listed universe.
    reconstructed=summary(previous,old_records,old_records,
                          prior.get("source_major_history_sha256"))
    reconstructed["listed_total"]=prior["listed_total"]
    if reconstructed!=prior:
        raise CashflowError("untrusted_previous_cashflow_source")
    current_records={c:rec for c,rec in old_records.items() if c in members}
    now=summary(asof,members,current_records,fingerprint(bulk))
    verify_state(now,market,catalog,bulk,strict_fingerprint=True)
    return now


def plan(market,catalog,bulk,prior=None,limit=MAX_ISSUERS):
    asof,members=asof_members(market,catalog,bulk)
    if type(limit) is not int or not 1<=limit<=MAX_ISSUERS:
        raise CashflowError("invalid_cashflow_max_issuers")
    prior=retained_current_market_cashflow(market,catalog,bulk,prior)
    retained=prior["records"] if prior is not None else {}
    queues={"KOSPI":[],"KOSDAQ":[]}
    for code,row in members.items():
        record=bulk["records"].get(code)
        scope=matched_scope(record)
        if scope is None:
            continue
        priorrow=retained.get(code,{})
        if priorrow and priorrow.get("scope")!=scope:
            # Source revisions cannot silently combine CFS and OFS observations.
            continue
        observed=priorrow.get("years",{})
        pending=[y for y in YEARS if str(y) not in observed]
        if pending:
            # Finish already-partial real cash-flow chains before starting new.
            queues[row["market"]].append((len(pending),code,scope,pending))
    for m in queues:
        queues[m].sort()
    chosen=[]
    for market_name in ("KOSPI","KOSDAQ"):
        chosen+=queues[market_name][:limit//2]
    rest=sorted(queues["KOSPI"][limit//2:]+queues["KOSDAQ"][limit//2:])
    chosen+=rest[:limit-len(chosen)]
    return asof,members,retained,chosen


def _matching_amount(rows, identifiers):
    amounts=[]
    for row in rows:
        if (row.get("sj_div")!="CF" or
            row.get("account_id") not in identifiers or
            row.get("account_detail","-") not in ("","-")):
            continue
        val=_amount(row.get("thstrm_amount"))
        amounts.append(val)
    if not amounts or any(v is None for v in amounts):
        return None
    if len(set(amounts))!=1:
        return None
    return amounts[0]


def normalize_year(rows,*,code,corp,year,scope,asof,major_receipt):
    """A single request is specific to one company, year and CF scope."""
    if type(rows) is not list or len(rows)>18000:
        raise CashflowError("bad_single_full_statement_row_count")
    selected=[]
    receipts=set()
    for row in rows:
        if type(row) is not dict:
            raise CashflowError("invalid_single_full_statement_row")
        if (row.get("corp_code")!=corp or
            ("stock_code" in row and row["stock_code"]!=code) or
            str(row.get("bsns_year"))!=str(year) or
            row.get("reprt_code")!="11011" or
            ("fs_div" in row and row["fs_div"]!=scope)):
            raise CashflowError("single_full_statement_issuer_or_fiscal_mismatch")
        if row.get("sj_div")!="CF":
            continue
        if row.get("account_id") not in CFO_IDS|PPE_IDS|INTANGIBLE_IDS:
            continue
        if row.get("account_detail","-") not in ("-",""):
            continue
        if row.get("currency")!="KRW":
            raise CashflowError("non_krw_standard_cashflow")
        receipt=row.get("rcept_no")
        if not _valid_receipt(receipt,year,_day(asof)):
            raise CashflowError("invalid_cashflow_receipt_or_lookahead")
        receipts.add(receipt)
        selected.append(row)
    if len(receipts)>1:
        # Mixed disclosures cannot be one three-statement fiscal year.
        raise CashflowError("mixed_cashflow_receipt_revisions")
    receipt=next(iter(receipts),None)
    cfo=_matching_amount(selected,CFO_IDS)
    ppe=_matching_amount(selected,PPE_IDS)
    intangible=_matching_amount(selected,INTANGIBLE_IDS)
    # Standard IFRS "purchase" cash outflows are stored as nonnegative
    # expense amounts here; negative or missing signs require human review.
    if ppe is not None and ppe<0: ppe=None
    if intangible is not None and intangible<0: intangible=None
    capex=ppe+intangible if ppe is not None and intangible is not None else None
    fcf=cfo-capex if cfo is not None and capex is not None else None
    return {
        "year":year,"scope":scope,"annual_receipt_id":receipt,
        "major_annual_receipt_matched":(receipt is not None
                                         and receipt==major_receipt),
        "operating_cashflow_krw":cfo,
        "ppe_capex_krw":ppe,
        "intangible_capex_krw":intangible,
        "total_capex_krw":capex,
        "free_cashflow_krw":fcf,
        "raw_source_account_rows_published":False,
        "historical_PIT_certified":False,
    }


def summary(asof,members,records,major_source_hash):
    count={"attempted_company_years":0,"valid_cashflow_company_years":0,
           "complete_capex_pair_company_years":0,
           "fcf_company_years":0,"receipt_matched_fcf_company_years":0,
           "three_year_fcf_receipt_matched_issuers":0,
           "most_recent_year_receipt_matched_fcf_issuers":0}
    for code,r in records.items():
        if code not in members or type(r) is not dict or r.get("scope") not in ("CFS","OFS"):
            raise CashflowError("invalid_cashflow_checkpoint_issuer")
        years=r.get("years")
        if type(years) is not dict or not set(years)<=set(map(str,YEARS)):
            raise CashflowError("invalid_cashflow_checkpoint_year")
        three=[]
        for y,z in years.items():
            if (type(z) is not dict or z.get("year")!=int(y)
                or z.get("scope")!=r["scope"]
                or z.get("raw_source_account_rows_published") is not False
                or z.get("historical_PIT_certified") is not False):
                raise CashflowError("invalid_cashflow_checkpoint_provenance")
            for n in ("operating_cashflow_krw","ppe_capex_krw",
                      "intangible_capex_krw","total_capex_krw","free_cashflow_krw"):
                v=z.get(n)
                if v is not None and (type(v) is not int or abs(v)>10**18):
                    raise CashflowError("invalid_cashflow_checkpoint_amount")
            cfo=z.get("operating_cashflow_krw")
            ppe=z.get("ppe_capex_krw")
            intangible=z.get("intangible_capex_krw")
            capex=z.get("total_capex_krw")
            fcf=z.get("free_cashflow_krw")
            if capex!=(ppe+intangible if ppe is not None and intangible is not None else None):
                raise CashflowError("untrusted_cashflow_capex_sum")
            if fcf!=(cfo-capex if cfo is not None and capex is not None else None):
                raise CashflowError("untrusted_cashflow_fcf_formula")
            if (z.get("major_annual_receipt_matched") is True and
                not isinstance(z.get("annual_receipt_id"),str)):
                raise CashflowError("cashflow_missing_receipt")
            count["attempted_company_years"]+=1
            count["valid_cashflow_company_years"]+=int(cfo is not None)
            count["complete_capex_pair_company_years"]+=int(capex is not None)
            count["fcf_company_years"]+=int(fcf is not None)
            matched=fcf is not None and z.get("major_annual_receipt_matched") is True
            count["receipt_matched_fcf_company_years"]+=int(matched)
            if y=="2025" and matched:
                count["most_recent_year_receipt_matched_fcf_issuers"]+=1
            three.append((int(y),matched))
        if len(three)==3 and all(v for _,v in three):
            count["three_year_fcf_receipt_matched_issuers"]+=1
    return {"schema":SCHEMA,"asof":asof,"listed_total":len(members),
            "source_major_history_sha256":major_source_hash,
            "source_fiscal_years":list(YEARS),
            "source":"OpenDART fnlttSinglAcntAll exact IFRS CF account IDs",
            "cashflow_accounting_consolidated_with_major_source":False,
            "three_year_fcf_financial15_certified":False,
            "current_year_fcf_valuation15_certified":False,
            "official_six_pillar_100pt_certified":False,
            "historical_PIT_certified":False,
            "raw_dart_rows_published":False,
            "metrics":count,
            "records":dict(sorted(records.items()))}


def verify_state(state,market,catalog,bulk,strict_fingerprint=True):
    asof,members=asof_members(market,catalog,bulk)
    if (type(state) is not dict or state.get("schema")!=SCHEMA or
        state.get("asof")!=asof or state.get("listed_total")!=len(members) or
        state.get("historical_PIT_certified") is not False or
        state.get("raw_dart_rows_published") is not False or
        state.get("official_six_pillar_100pt_certified") is not False or
        type(state.get("records")) is not dict):
        raise CashflowError("invalid_cashflow_snapshot_identity")
    if (strict_fingerprint and
        state.get("source_major_history_sha256")!=fingerprint(bulk)):
        raise CashflowError("cashflow_source_changed_after_rebase")
    # A newer bulk archive may append unrelated filings. Permit that
    # without a needless refetch, but NEVER carry a "receipt matched" FCF
    # across a changed or missing major-account filing for the same issuer.
    for code,issuer in state["records"].items():
        if type(issuer) is not dict:
            raise CashflowError("invalid_cashflow_checkpoint_issuer")
        scope=issuer.get("scope")
        current=bulk.get("records",{}).get(code,{}).get("years",{})
        for year,record in issuer.get("years",{}).items():
            if (type(record) is dict and
                record.get("major_annual_receipt_matched") is True):
                major=current.get(year,{}).get(scope,{})
                if (type(major) is not dict or
                    major.get("receipt_id")!=record.get("annual_receipt_id")):
                    raise CashflowError("cashflow_major_receipt_changed")
    calculated=summary(asof,members,state["records"],
                       state.get("source_major_history_sha256"))
    if state!=calculated:
        raise CashflowError("cashflow_summary_or_snapshot_tampered")
    return True


def collect(market,catalog,bulk,prior,*,key,session,corp_cache=None,
            issuer_limit=MAX_ISSUERS,on_progress=None):
    asof,members,retained,chosen=plan(
        market,catalog,bulk,prior=prior,limit=issuer_limit)
    state=dict(retained)
    if not chosen:
        return summary(asof,members,state,fingerprint(bulk)),0
    if type(key) is not str or len(key)<20:
        raise CashflowError("missing_cashflow_api_key")
    mapping=verified_company_mapping_cache(corp_cache,asof,members)
    calls=0
    if mapping is None:
        try:
            resp=session.get("https://opendart.fss.or.kr/api/corpCode.xml",
                             params={"crtfc_key":key},timeout=(20,95))
            calls+=1
            resp.raise_for_status()
            mapping=_mapping(resp.content,set(members))
        except Exception:
            raise CashflowError("cashflow_corp_mapping_unavailable") from None
    cutoff=time.monotonic()+MAX_SECONDS
    for _,code,scope,pending in chosen:
        if code not in mapping:
            # Unmapped code not confused with observed zero-CF: try again in
            # later scheduled census, never persist an invented company record.
            continue
        years=dict(state.get(code,{"scope":scope,"years":{}})["years"])
        for year in pending:
            if calls>=MAX_SOURCE_CALLS or time.monotonic()>=cutoff:
                raise CashflowError("cashflow_safe_call_or_time_budget")
            try:
                resp=session.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                    params={"crtfc_key":key,"corp_code":mapping[code],
                            "bsns_year":str(year),"reprt_code":"11011",
                            "fs_div":scope},timeout=32)
                calls+=1
                resp.raise_for_status()
                payload=resp.json()
            except Exception:
                raise CashflowError("cashflow_provider_http_or_json_failure") from None
            if type(payload) is not dict:
                raise CashflowError("cashflow_unexpected_provider_payload")
            status=payload.get("status")
            if status=="013":
                rows=[]
            elif status=="000":
                rows=payload.get("list")
                if type(rows) is not list or not rows:
                    raise CashflowError("cashflow_bad_success_response")
            else:
                raise CashflowError("cashflow_provider_stop")
            major=bulk["records"][code]["years"][str(year)][scope]
            result=normalize_year(rows,code=code,corp=mapping[code],year=year,
                                  scope=scope,asof=asof,
                                  major_receipt=major["receipt_id"])
            years[str(year)]=result
            state[code]={"scope":scope,"years":years}
            snap=summary(asof,members,state,fingerprint(bulk))
            if on_progress:
                on_progress(snap)
    return summary(asof,members,state,fingerprint(bulk)),calls


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market",default="docs/data.json")
    p.add_argument("--catalog",default="docs/v22-market-catalog.json")
    p.add_argument("--major",default="docs/v3-dart-bulk-history.json")
    p.add_argument("--state",default="docs/v3-dart-cashflow-reference.json")
    p.add_argument("--cache",default=".cache/v3-dart-company-codes.json")
    p.add_argument("--issuer-limit",type=int,default=MAX_ISSUERS)
    p.add_argument("--mode",choices=("dry-run","live","verify","rebind"),default="dry-run")
    a=p.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))
    market=read(a.market)
    catalog=read(a.catalog)
    bulk=read(a.major)
    state_path=Path(a.state)
    prior=read(a.state) if state_path.is_file() else None
    asof,members,_,chosen=plan(market,catalog,bulk,prior=prior,limit=a.issuer_limit)
    if a.mode=="dry-run":
        print(json.dumps({"status":"dry_run_no_api","asof":asof,
             "eligible_next_company_count":len(chosen),"source_calls":0,
             "cashflow_scores_certified":False},sort_keys=True))
        return
    if a.mode=="rebind":
        # Only after a git rebase: no network, no account substitution.
        # verify_state checks each previously receipt-matched statement
        # against the current major-history issuer/year/scope/receipt.
        if prior is None:
            raise CashflowError("cashflow_verified_state_missing")
        refreshed=retained_current_market_cashflow(
            market,catalog,bulk,prior)
        refreshed=summary(asof,members,refreshed["records"],fingerprint(bulk))
        write_json(state_path,refreshed)
        print(json.dumps({"status":"receipt_rebound_same_market_date",
             "source_calls":0,"persisted_issuer_years":
             refreshed["metrics"]["attempted_company_years"],
             "full_v3_ready":False},sort_keys=True))
        return
    if a.mode=="verify":
        if prior is None:
            raise CashflowError("cashflow_verified_state_missing")
        verify_state(prior,market,catalog,bulk)
        print("verified_source_only_dart_cashflow_checkpoint")
        return
    import requests
    key=os.getenv("DART_API_KEY","")
    cache=read(a.cache) if Path(a.cache).is_file() else None
    saved=0
    def checkpoint(state):
        nonlocal saved
        write_json(state_path,state)
        saved+=1
    try:
        finished,calls=collect(market,catalog,bulk,prior,key=key,
                 session=requests.Session(),corp_cache=cache,
                 issuer_limit=a.issuer_limit,on_progress=checkpoint)
    except CashflowError as exc:
        if not saved:
            raise
        print(json.dumps({"status":"partial_observed_cashflow_saved",
             "source_year_checkpoints":saved,
             "halt_cause":str(exc),"full_v3_ready":False},sort_keys=True))
        return
    write_json(state_path,finished)
    print(json.dumps({"status":"source_derived_cashflow_research_only",
        "actual_fiscal_source_calls":calls,
        "persisted_issuer_years":finished["metrics"]["attempted_company_years"],
        "observed_cfo_years":finished["metrics"]["valid_cashflow_company_years"],
        "observed_fcf_years":finished["metrics"]["fcf_company_years"],
        "fully_matched_three_fcf_issuers":
        finished["metrics"]["three_year_fcf_receipt_matched_issuers"],
        "full_v3_ready":False},sort_keys=True))


if __name__=="__main__":
    main()
