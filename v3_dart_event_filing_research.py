"""Cross-check large KRX derived returns with REAL dated OpenDART filing receipts.

Research-only: an abnormal price jump is NOT proof of a split, merger or
ex-rights adjustment. Even an issuer filing about a corporate action does NOT
prove the action's effective date or price adjustment ratio. Historical stock
code vs today's DART corpCode mapping is likewise UNVERIFIED.

The checked-in public repository, logs and GitHub Pages NEVER receive KRX
derived per-stock risk values or original DART filing data. Use only PRIVATE
GitHub source evidence, ephemeral OpenDART responses, and private summarized
filing *receipt* metadata. No trades, rankings or V3 point promotion.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import gzip
from hashlib import sha256
from io import BytesIO
import json
import os
import re

from official_probe.clients import DARTClient,ProbeError
from v3_krx_derived_corporate_action_risk import _read_existing as risk_read
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_derived_bridges import _readback_with_retry

SCHEMA="v3-private-dart-filing-receipt-risk-crosscheck-v1"
BASE_DAY="2016-12-16"
COHORTS=9
RISK_PATH="derived/quality/price-discontinuities-"+BASE_DAY+".json.gz"
OUTPUT="derived/quality/dart-risk-filing-receipts-2016-12-16.json.gz"
MAX_ISSUERS=6
MAX_PAGES=2
PAGE_COUNT=100
PERIOD_BEGIN="2015-01-01"
PERIOD_END="2017-12-31"
MAX_COMPRESSED_BYTES=130_000
MAX_DART_CALLS=1+MAX_ISSUERS*MAX_PAGES
CATEGORY=(
    ("company_split",re.compile("회사분할|분할합병|분할결정|인적분할|물적분할")),
    ("stock_split_or_reverse",re.compile("주식분할|주식병합|액면분할|액면병합")),
    ("merger",re.compile("합병")),
    ("capital_reduction",re.compile("감자")),
    ("share_issuance",re.compile("무상증자|유상증자|증자결정")),
    ("share_exchange",re.compile("주식의.*교환|주식의.*이전")),
)


class EventEvidenceError(ValueError):
    """Fixed diagnostic, never vendor message, URL or API credential."""


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=True,
                      separators=(",",":"),allow_nan=False).encode("ascii")


def _closed_date(value,start,end):
    if type(value) is not str or not re.fullmatch(r"\d{8}",value):
        raise EventEvidenceError("invalid_dart_disclosure_date")
    try: d=date.fromisoformat(value[:4]+"-"+value[4:6]+"-"+value[6:])
    except ValueError:raise EventEvidenceError("invalid_dart_disclosure_date") from None
    if not start<=d<=end:
        raise EventEvidenceError("out_of_scope_dart_filing_date")
    return d.isoformat()


def _selected_risks(flags):
    if not isinstance(flags,list) or len(flags)>300:
        raise EventEvidenceError("invalid_private_risk_list")
    strongest={}
    for item in flags:
        k=item["issuer_market"]
        code=k.split(":")[1]
        if not re.fullmatch(r"[0-9]{6}",code):
            continue
        change=item["quantized_change_bp"]
        if type(change) is not int:
            raise EventEvidenceError("invalid_private_risk_change")
        strongest[code]=max(abs(change),strongest.get(code,0))
    return [code for code,_ in sorted(strongest.items(),
                                     key=lambda q:(-q[1],q[0]))[:MAX_ISSUERS]]


def _classify(name):
    if type(name) is not str or len(name)>600:
        raise EventEvidenceError("invalid_dart_report_name")
    return [kind for kind,pattern in CATEGORY if pattern.search(name)]


def _one_issuer(client,corp_code,stock_code):
    start=date.fromisoformat(PERIOD_BEGIN)
    end=date.fromisoformat(PERIOD_END)
    events=[]
    pages=0
    full_window_checked=False
    for page in range(1,MAX_PAGES+1):
        try:
            doc=client._api("list.json",corp_code=corp_code,
                            bgn_de=PERIOD_BEGIN.replace("-",""),
                            end_de=PERIOD_END.replace("-",""),
                            page_no=page,page_count=PAGE_COUNT)
        except Exception:
            raise EventEvidenceError("official_dart_filing_query_unavailable") from None
        pages+=1
        rows=doc.get("list",[])
        if not isinstance(rows,list) or len(rows)>PAGE_COUNT:
            raise EventEvidenceError("invalid_dart_list_page")
        if doc.get("status")=="013":
            # Absence of this data is NOT proof the issuer never had an event.
            full_window_checked=True
            break
        total_page=doc.get("total_page")
        try: total_page=int(total_page)
        except (TypeError,ValueError):
            raise EventEvidenceError("missing_dart_pagination_certificate") from None
        if not 1<=total_page<=10000 or page>total_page:
            raise EventEvidenceError("invalid_dart_total_pages")
        for row in rows:
            if not isinstance(row,dict) or row.get("corp_code")!=corp_code:
                raise EventEvidenceError("dart_issuer_identity_mismatch")
            receipt=row.get("rcept_no")
            if type(receipt) is not str or not re.fullmatch(r"[0-9]{14}",receipt):
                raise EventEvidenceError("bad_dart_official_receipt")
            received=_closed_date(row.get("rcept_dt"),start,end)
            for kind in _classify(row.get("report_nm")):
                events.append({"receipt":receipt,"filing_date":received,
                               "category":kind,
                               "historical_price_effective_date_verified":False})
        if page>=total_page:
            full_window_checked=True
            break
    dedup={(x["receipt"],x["category"]):x for x in events}
    return {
        "issuer_code":stock_code,
        "corp_code":corp_code,
        "pages_examined":pages,
        "all_filing_pages_examined_within_date_window":full_window_checked,
        "relevant_corporate_action_filing_receipts":
            sorted(dedup.values(),key=lambda x:(x["filing_date"],x["receipt"],x["category"])),
        "historical_stock_corporation_identity_verified":False,
        "event_price_adjustment_verified":False,
        "unmatched_risk_is_verified_no_event":False,
    }


def _read_output(raw):
    try:
        if type(raw) is not bytes or not 0<len(raw)<MAX_COMPRESSED_BYTES:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(raw)) as inp:
            plain=inp.read(2_000_001)
        if len(plain)>2_000_000:
            raise ValueError()
        info=json.loads(plain)
        if (info.get("schema")!=SCHEMA
            or info.get("risk_baseline_end")!=BASE_DAY
            or info.get("target_limit")!=MAX_ISSUERS
            or info.get("krx_market_api_calls")!=0
            or info.get("dart_original_disclosures_archived") is not False
            or info.get("historical_corporate_action_verified") is not False
            or info.get("confirmed_adjusted_returns") is not False
            or info.get("ias_flow_ledger_authenticated") is not False
            or info.get("v3_six_pillar_trade_points") is not None):
            raise ValueError()
        return info
    except Exception:
        raise EventEvidenceError("private_dart_receipt_summary_corrupt") from None


def run(*,storage,client):
    if storage.repository!="ykvic1/korean-stock-private-data":
        raise EventEvidenceError("not_a_private_derived_research_repository")
    storage.verify_private()
    existing,_=storage.get_content(OUTPUT)
    if existing is not None:
        prev=_read_output(existing)
        return {"status":"verified_existing_dart_filing_research","issuers_attempted":
                prev["issuers_attempted"],"provider_calls":0,
                "filing_receipt_categories_observed":prev["filing_evidence_count"],
                "price_corporate_action_verified":False}
    body,_=storage.get_content(RISK_PATH)
    if body is None:
        raise EventEvidenceError("missing_confirmed_krx_derived_risk_baseline")
    flag_data=risk_read(body,BASE_DAY,COHORTS)
    selected=_selected_risks(flag_data["flagged_issuer_periods"])
    if not selected:
        raise EventEvidenceError("no_historical_issuer_risk_targets")
    try: mapping=client.corp_mapping()
    except Exception:
        raise EventEvidenceError("official_dart_company_mapping_not_available") from None
    results=[]
    for code in selected:
        if code not in mapping:
            results.append({"issuer_code":code,"status":"unmapped_in_current_dart_corpcodes",
                            "historical_identity_verified":False})
        else:
            entry=_one_issuer(client,mapping[code],code)
            entry["status"]="filing_receipt_research_only"
            results.append(entry)
    matched=sum(x["status"]=="filing_receipt_research_only" for x in results)
    receipt_total=sum(len(x.get("relevant_corporate_action_filing_receipts",[]))
                      for x in results)
    calls=len(getattr(client,"research_request_timings",[]))
    if calls>MAX_DART_CALLS or calls<1:
        raise EventEvidenceError("official_filing_probe_call_budget_invalid")
    compact={
        "schema":SCHEMA,"risk_baseline_end":BASE_DAY,
        "target_limit":MAX_ISSUERS,"issuers_attempted":len(selected),
        "historical_issuer_mapping_current_snapshot_only":True,
        "issuer_mapping_currently_available":matched,
        "filing_evidence_count":receipt_total,
        "research_window_start":PERIOD_BEGIN,"research_window_end":PERIOD_END,
        "dart_api_calls":calls,
        "krx_market_api_calls":0,
        "issuer_results":results,
        "dart_original_disclosures_archived":False,
        "historical_corporate_action_verified":False,
        "confirmed_adjusted_returns":False,
        "ias_flow_ledger_authenticated":False,
        "v3_six_pillar_trade_points":None,
        "no_filing_found_does_not_prove_no_corporate_action":True,
        "private_noncommercial_research_only":True,
    }
    packed=gzip.compress(_canonical(compact),compresslevel=9,mtime=0)
    if len(packed)>=MAX_COMPRESSED_BYTES:
        raise EventEvidenceError("private_dart_metadata_oversized")
    storage.put_new(OUTPUT,packed,"Save private DART receipt-level price-event research")
    _readback_with_retry(storage,OUTPUT,packed)
    _read_output(packed)
    return {"status":"private_dart_filing_evidence_saved",
            "issuers_attempted":len(selected),
            "issuers_with_dart_mapping":matched,
            "filing_receipt_categories_observed":receipt_total,
            "provider_calls":calls,
            "stored_compressed_derived_bytes":len(packed),
            "krx_market_api_calls":0,"raw_market_files_saved":0,
            "price_corporate_action_verified":False,
            "ias20_verified":False}


def main():
    repo=os.getenv("V3_PRIVATE_ARCHIVE_REPO")
    token=os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","")
    key=os.getenv("DART_API_KEY","")
    if repo!="ykvic1/korean-stock-private-data" or not token or not key:
        print('{"status":"blocked","reason":"official_dart_or_private_archive_unavailable"}')
        return 1
    try:
        result=run(storage=PrivateGitStorage(repo,token),client=DARTClient(key))
        print(json.dumps(result,sort_keys=True))
        return 0
    except EventEvidenceError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),
                          "original_source_logged":False},sort_keys=True))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"unknown_official_dart_or_private_failure","original_source_logged":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
