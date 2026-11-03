"""Bounded official 2025 owners' profit source evidence for near-100 issuers.

Only codes from the independently checked missing-earnings-yield priority list
are queried. No IFRS account / different receipt / different scope means NULL,
not zero. No current score is changed by this source acquisition step.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
from typing import Callable

from v3_dart_bulk_history import (
    _amount, _mapping, _valid_receipt, _stable_digest,
    verified_company_mapping_cache, write_json, BulkHistoryError,
)

SCHEMA = "v3-official-fy2025-owner-profit-source-0.1"
MAX_PER_RUN = 20
PROFIT_IDS = {
    "CFS": frozenset((
        "ifrs-full_ProfitLossAttributableToOwnersOfParent",
        "ifrs_ProfitLossAttributableToOwnersOfParent")),
    "OFS": frozenset(("ifrs-full_ProfitLoss", "ifrs_ProfitLoss")),
}
CODE = re.compile(r"[0-9]{6}\Z")


class ProfitProbeError(ValueError):
    """Only source-independent reason codes may reach workflow logs."""


def _day(raw):
    if type(raw) is date:
        return raw  # Internal validated cutoff passed by plan().
    try:
        value = date.fromisoformat(raw)
        if value.isoformat() == raw:
            return value
    except (ValueError, TypeError):
        pass
    raise ProfitProbeError("untrusted_profit_source_day")


def plan(catalog, book, priority, prior=None, limit=MAX_PER_RUN):
    day = _day(catalog.get("date"))
    if (not isinstance(catalog.get("rows"), list)
        or catalog.get("listed_total") != len(catalog["rows"])
        or not 1 <= len(catalog["rows"]) <= 6000
        or type(limit) is not int or not 1 <= limit <= MAX_PER_RUN
        or book.get("asof") != day.isoformat()
        or book.get("historical_point_in_time_verified") is not False
        or priority.get("asof") != day.isoformat()
        or priority.get("no_points_added") is not True
        or priority.get("official_trade_score_certified") is not False
        or priority.get("schema") !=
           "v3-observed-near100-source-gap-priority-0.1"):
        raise ProfitProbeError("misaligned_owner_profit_source_priority")
    members = {}
    for row in catalog["rows"]:
        code = row.get("code")
        if (type(code) is not str or not CODE.fullmatch(code)
            or code in members or row.get("market") not in ("KOSPI","KOSDAQ")
            or type(row.get("name")) is not str or not row["name"].strip()):
            raise ProfitProbeError("invalid_profit_issuer_roster")
        members[code] = (row["name"], row["market"])
    books = book.get("records")
    if not isinstance(books,list) or len(books)>6000:
        raise ProfitProbeError("missing_profit_book")
    books = {r.get("code"):r for r in books}
    queue = priority.get("official_2025_owners_profit_missing_priority_codes")
    if (not isinstance(queue,list) or len(queue) !=
        priority.get("official_2025_owners_profit_missing_priority_count")
        or queue != sorted(set(queue)) or not set(queue) <= set(members)):
        raise ProfitProbeError("tampered_owner_profit_priority")
    expected = {}
    for code in queue:
        record = books.get(code) or {}
        annual = record.get("annual") or {}
        receipt = annual.get("receipt_id")
        scope = annual.get("fs_div")
        if (record.get("status") != "observed"
            or scope not in PROFIT_IDS or annual.get("year") != 2025
            or annual.get("currency") != "KRW"
            or not _valid_receipt(receipt, 2025, day)
            or not isinstance(annual.get("values"),dict)
            or annual["values"].get("owners_profit") is not None):
            raise ProfitProbeError("priority_not_unobserved_annual_profit")
        expected[code] = {
            "code":code,"name":members[code][0],"market":members[code][1],
            "fiscal_year":2025,"fs_div":scope,"annual_receipt_id":receipt,
        }
    retained = {}
    if prior is not None:
        if (type(prior) is not dict or prior.get("schema") != SCHEMA
            or prior.get("investment_grade_score_certified") is not False
            or prior.get("historical_pit_verified") is not False
            or type(prior.get("records")) is not dict
            or _day(prior.get("market_asof")) > day):
            raise ProfitProbeError("untrusted_previous_profit_evidence")
        for code, record in prior["records"].items():
            if code not in expected or not isinstance(record,dict):
                continue
            original = expected[code]
            if any(record.get(k) != original[k] for k in original):
                continue
            _validated_record(record,day)
            retained[code] = record
    missing = [code for code in queue if code not in retained]
    # Fair interleaving between KOSPI and KOSDAQ, no forced fake candidates.
    groups = {name:[code for code in missing
                    if members[code][1] == name]
              for name in ("KOSPI","KOSDAQ")}
    target = []
    while len(target)<limit and (groups["KOSPI"] or groups["KOSDAQ"]):
        for market in ("KOSPI","KOSDAQ"):
            if groups[market] and len(target)<limit:
                target.append(groups[market].pop(0))
    return day.isoformat(), members, expected, retained, target


def _validated_record(record, cutoff):
    if (type(record) is not dict or record.get("status") not in
        ("observed_owner_profit","missing_standard_account")
        or record.get("fiscal_year") != 2025
        or record.get("fs_div") not in PROFIT_IDS
        or not _valid_receipt(record.get("annual_receipt_id"),2025,_day(cutoff))
        or record.get("market") not in ("KOSPI","KOSDAQ")
        or type(record.get("name")) is not str or not record["name"]
        or type(record.get("code")) is not str
        or not CODE.fullmatch(record["code"])
        or _day(record.get("observed_on")) > _day(cutoff)
        or record.get("raw_provider_rows_published") is not False):
        raise ProfitProbeError("invalid_profit_evidence_record")
    value = record.get("owners_profit_krw")
    account = record.get("source_account_id")
    if record["status"] == "observed_owner_profit":
        if (type(value) is not int or abs(value) > 10**18
            or account not in PROFIT_IDS[record["fs_div"]]):
            raise ProfitProbeError("invalid_profit_evidence_value")
    elif value is not None or account is not None:
        raise ProfitProbeError("missing_account_misrepresented_as_zero")


def parse_response(payload, *,corp, base, on_day):
    if not isinstance(payload,dict):
        raise ProfitProbeError("invalid_annual_profit_response")
    status=payload.get("status")
    if status == "013":
        rows=[]
    elif status == "000" and isinstance(payload.get("list"),list):
        rows=payload["list"]
        if not rows:
            raise ProfitProbeError("empty_annual_profit_success_response")
    else:
        raise ProfitProbeError("official_profit_provider_status_unavailable")
    if len(rows)>18000:
        raise ProfitProbeError("oversized_annual_profit_response")
    matches=[]
    for row in rows:
        if (type(row) is not dict
            or row.get("corp_code") != corp
            or ("stock_code" in row and row["stock_code"] != base["code"])
            or str(row.get("bsns_year")) != "2025"
            or row.get("reprt_code") != "11011"
            or ("fs_div" in row and row["fs_div"] != base["fs_div"])):
            raise ProfitProbeError("annual_profit_source_identity_mismatch")
        if (row.get("sj_div") not in ("IS","CIS")
            or row.get("account_id") not in PROFIT_IDS[base["fs_div"]]
            or row.get("account_detail","-") not in ("","-")):
            continue
        if row.get("currency") != "KRW":
            raise ProfitProbeError("annual_profit_currency_not_krw")
        if row.get("rcept_no") != base["annual_receipt_id"]:
            raise ProfitProbeError("annual_profit_receipt_not_matched")
        value=_amount(row.get("thstrm_amount"))
        if value is None:
            raise ProfitProbeError("annual_profit_amount_not_integer_krw")
        matches.append((row["account_id"],value))
    values=set(v for _,v in matches)
    if len(values)>1:
        raise ProfitProbeError("conflicting_official_owner_profit_accounts")
    result={
        **base, "observed_on":on_day, "raw_provider_rows_published":False,
        "status":"observed_owner_profit" if matches else "missing_standard_account",
        "owners_profit_krw":next(iter(values)) if matches else None,
        "source_account_id":sorted(matches)[0][0] if matches else None,
    }
    _validated_record(result,on_day)
    return result


def snapshot(day, catalog, book, priority, records):
    if not isinstance(records,dict):
        raise ProfitProbeError("invalid_profit_source_records")
    observed=sum(x["status"]=="observed_owner_profit" for x in records.values())
    report={
        "schema":SCHEMA, "market_asof":day,
        "fiscal_year":2025, "candidate_priority_asof":priority["asof"],
        "source_book_sha256":_stable_digest(book),
        "source_catalog_sha256":_stable_digest(catalog),
        "source_priority_sha256":_stable_digest(priority),
        "listed_total":catalog["listed_total"],
        "target_95weight_issuers":
            priority["official_2025_owners_profit_missing_priority_count"],
        "observed_official_owner_profit_issuers":observed,
        "missing_standard_account_issuers":len(records)-observed,
        "investment_grade_score_certified":False,
        "historical_pit_verified":False,
        "missing_account_is_not_zero":True,
        "account_value_used_in_scores":False,
        "records":dict(sorted(records.items())),
    }
    return report


def verify(report, catalog, book, priority):
    day,members,expected,_,_ = plan(catalog,book,priority,None)
    if (type(report) is not dict or report.get("market_asof") != day
        or type(report.get("records")) is not dict):
        raise ProfitProbeError("invalid_profit_source_snapshot")
    records=report["records"]
    for code, rec in records.items():
        if code not in expected or not isinstance(rec,dict):
            raise ProfitProbeError("unexpected_profit_source_issuer")
        if any(rec.get(k)!=v for k,v in expected[code].items()):
            raise ProfitProbeError("profit_source_company_or_receipt_changed")
        _validated_record(rec,day)
    if report!=snapshot(day,catalog,book,priority,records):
        raise ProfitProbeError("tampered_profit_source_summary")
    return True


def collect(catalog,book,priority,prior,*,key,session,cache=None,
            limit=MAX_PER_RUN,on_progress:Callable|None=None):
    day,members,expected,retained,selected=plan(
        catalog,book,priority,prior,limit)
    if not selected:
        result=snapshot(day,catalog,book,priority,retained)
        verify(result,catalog,book,priority)
        return result,0
    if type(key) is not str or len(key)<20:
        raise ProfitProbeError("official_dart_api_key_missing")
    try:
        mapping=verified_company_mapping_cache(cache,day,members)
    except BulkHistoryError:
        raise ProfitProbeError("untrusted_official_corp_identity_cache") from None
    calls=0
    if mapping is None:
        try:
            res=session.get("https://opendart.fss.or.kr/api/corpCode.xml",
                            params={"crtfc_key":key},timeout=(20,95))
            calls+=1
            res.raise_for_status()
            mapping=_mapping(res.content,set(members))
        except Exception:
            raise ProfitProbeError("official_corp_identity_unavailable") from None
    for code in selected:
        if code not in mapping:
            # Missing mapping can be retried after a NEW, verified census.
            continue
        base=expected[code]
        try:
            response=session.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key":key,"corp_code":mapping[code],
                        "bsns_year":"2025","reprt_code":"11011",
                        "fs_div":base["fs_div"]}, timeout=(20,40))
            calls+=1
            response.raise_for_status()
            result=parse_response(
                response.json(),corp=mapping[code],base=base,on_day=day)
        except ProfitProbeError:
            raise
        except Exception:
            raise ProfitProbeError("official_annual_profit_transport_unavailable") from None
        retained[code]=result
        current=snapshot(day,catalog,book,priority,retained)
        if on_progress:
            on_progress(current)
    output=snapshot(day,catalog,book,priority,retained)
    verify(output,catalog,book,priority)
    return output,calls


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs",default="docs")
    p.add_argument("--cache",default=".cache/v3-dart-company-codes.json")
    p.add_argument("--mode",choices=("dry-run","live","verify"),default="dry-run")
    p.add_argument("--limit",type=int,default=MAX_PER_RUN)
    args=p.parse_args(argv)
    root=Path(args.docs)
    def read(filename):
        return json.loads((root/filename).read_text(encoding="utf8"))
    catalog=read("v22-market-catalog.json")
    book=read("v3-dart-book-reference.json")
    priority=read("v3-near-100-evidence-priority.json")
    target=root/"v3-dart-owner-profit-supplement.json"
    old=read(target.name) if target.exists() else None
    if args.mode=="verify":
        if old is None:
            print('{"status":"no_owner_profit_source_yet","source_api_calls":0}')
            return
        verify(old,catalog,book,priority)
        print(json.dumps({"status":"verified_saved_owner_profit_source",
                          "evidence_issuers":len(old["records"]),
                          "observed_profit_count":
                              old["observed_official_owner_profit_issuers"],
                          "score_promoted":False},sort_keys=True))
        return
    day,_,_,_,selected=plan(catalog,book,priority,old,args.limit)
    if args.mode=="dry-run":
        print(json.dumps({"status":"bounded_fy2025_profit_plan",
                          "candidate_95weight_issuers":
                              priority["official_2025_owners_profit_missing_priority_count"],
                          "selected_for_official_source":len(selected),
                          "source_api_calls":0,"score_promoted":False},
                         sort_keys=True))
        return
    import requests
    key=os.getenv("DART_API_KEY","")
    cache=read(args.cache) if Path(args.cache).exists() else None
    saved=0
    def checkpoint(report):
        nonlocal saved
        write_json(target,report)
        saved+=1
    try:
        result,calls=collect(catalog,book,priority,old,key=key,
                             session=requests.Session(),cache=cache,
                             limit=args.limit,on_progress=checkpoint)
    except ProfitProbeError as exc:
        if not saved:
            raise
        print(json.dumps({"status":"partial_profit_evidence_saved",
                          "reason":str(exc),"checkpoints":saved,
                          "score_promoted":False},sort_keys=True))
        return
    write_json(target,result)
    print(json.dumps({"status":"official_owner_profit_source_only",
                      "provider_calls":calls,
                      "observed_profit_count":
                          result["observed_official_owner_profit_issuers"],
                      "remaining_priority_codes":
                          result["target_95weight_issuers"]-len(result["records"]),
                      "score_promoted":False},sort_keys=True))


if __name__=="__main__":
    main()
