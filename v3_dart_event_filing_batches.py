"""Resume-safe private OpenDART corporate-filing receipts for KRX risk cases.

This appends ONLY research metadata about official receipts; no share-price
adjustment is inferred, no raw filings or prices are stored, no IAS score is
created. Independent stock-to-corporation historical identity remains unknown.
"""
from __future__ import annotations
import base64
from hashlib import sha256
import gzip
import json
import os
import re

from v3_dart_event_filing_research import (
    DARTClient, EventEvidenceError, MAX_ISSUERS, PERIOD_BEGIN, PERIOD_END,
    _one_issuer, _read_output, RISK_PATH, OUTPUT as FIRST_FILE)
from v3_krx_derived_corporate_action_risk import _read_existing as read_risk
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_derived_bridges import _readback_with_retry

SCHEMA="v3-private-dart-event-receipts-rolling-v1"
CHECKPOINT="derived/quality/dart-receipt-checkpoint.json"
MAX_NEXT_ISSUERS=10
MAX_BYTES=150_000


class DARTEventBatchError(ValueError):
    pass


def _canonical(x):
    return json.dumps(x,sort_keys=True,separators=(",",":"),
                      ensure_ascii=True,allow_nan=False).encode("ascii")


def _ranking(flags):
    greatest={}
    for row in flags:
        issuer=row["issuer_market"].split(":")[-1]
        if not re.fullmatch(r"[0-9]{6}",issuer):
            continue
        greatest[issuer]=max(greatest.get(issuer,0),abs(row["quantized_change_bp"]))
    return [code for code,_ in sorted(greatest.items(),
                                    key=lambda v:(-v[1],v[0]))]


def _batch_file(start,end):
    return f"derived/quality/dart-batches/2016-12-16_{start:03d}_{end:03d}.json.gz"


def _check_report(blob,start,end):
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_BYTES:
            raise ValueError()
        from io import BytesIO
        with gzip.GzipFile(fileobj=BytesIO(blob)) as src:
            raw=src.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError()
        obj=json.loads(raw)
        if (obj.get("schema")!=SCHEMA or obj.get("first_issuer_index")!=start
            or obj.get("next_issuer_index")!=end
            or obj.get("risk_baseline_end")!="2016-12-16"
            or obj.get("source_krx_calls")!=0
            or obj.get("original_dart_filing_rows_saved") is not False
            or obj.get("corporate_actions_certified") is not False
            or obj.get("v3_scores_certified") is not False
            or len(obj.get("issuer_results",[]))!=end-start):
            raise ValueError()
        return obj
    except Exception:
        raise DARTEventBatchError("invalid_private_dart_batch_evidence") from None


def _check_cursor(raw,ranked,risks_sha):
    try:
        x=json.loads(raw)
        n=x["next_issuer_index"]
        if (x.get("schema")!=SCHEMA or x.get("risks_sha256")!=risks_sha
            or type(n) is not int or not MAX_ISSUERS<=n<=len(ranked)
            or x.get("total_historical_risk_issuers")!=len(ranked)
            or x.get("raw_dart_source_retained") is not False
            or x.get("corporate_action_verified") is not False
            or type(x.get("last_file")) is not str
            or not x["last_file"].startswith("derived/quality/")
            or type(x.get("last_file_sha256")) is not str
            or not re.fullmatch(r"[a-f0-9]{64}",x["last_file_sha256"])
            or type(x.get("total_disclosure_categories")) is not int
            or x["total_disclosure_categories"]<0):
            raise ValueError()
        return x
    except Exception:
        raise DARTEventBatchError("invalid_dart_research_cursor") from None


def _save_cursor(private,data,sha):
    body=_canonical(data)
    if len(body)>1600:
        raise DARTEventBatchError("oversized_dart_research_cursor")
    if sha is None:
        private.put_new(CHECKPOINT,body,"Initialize historic DART filing research cursor")
    else:
        private.transport("PUT","contents/"+CHECKPOINT,{
            "message":"Advance limited historic DART filing research cursor",
            "sha":sha,"content":base64.b64encode(body).decode("ascii")})
    _readback_with_retry(private,CHECKPOINT,body)


def run(*,private,dart):
    if private.repository!="ykvic1/korean-stock-private-data":
        raise DARTEventBatchError("must_use_private_derived_archive")
    private.verify_private()
    raw_risks,_=private.get_content(RISK_PATH)
    if raw_risks is None:
        raise DARTEventBatchError("verified_historical_krx_risks_missing")
    try:
        risks=read_risk(raw_risks,"2016-12-16",9)
    except Exception:
        raise DARTEventBatchError("invalid_private_historical_risk_evidence") from None
    rank=_ranking(risks["flagged_issuer_periods"])
    digest=sha256(raw_risks).hexdigest()
    if len(rank)<MAX_ISSUERS:
        raise DARTEventBatchError("insufficient_historical_risk_universe")
    data,sha=private.get_content(CHECKPOINT)
    if data is None:
        first,_=private.get_content(FIRST_FILE)
        if first is None:
            raise DARTEventBatchError("first_official_dart_pilot_not_verified")
        prev=_read_output(first)
        if ([x["issuer_code"] for x in prev["issuer_results"]]
            != rank[:MAX_ISSUERS]):
            raise DARTEventBatchError("first_dart_pilot_unmatched_risk_ranking")
        point={"schema":SCHEMA,"risks_sha256":digest,
               "next_issuer_index":MAX_ISSUERS,
               "total_historical_risk_issuers":len(rank),
               "last_file":FIRST_FILE,"last_file_sha256":sha256(first).hexdigest(),
               "total_disclosure_categories":prev["filing_evidence_count"],
               "raw_dart_source_retained":False,
               "corporate_action_verified":False}
        _save_cursor(private,point,None)
        return {"status":"adopted_verified_real_dart_initial_six",
                "processed_issuers":MAX_ISSUERS,
                "total_historical_risk_issuers":len(rank),
                "cumulative_filing_categories":prev["filing_evidence_count"],
                "dart_provider_calls":0,"raw_price_stored":0}
    point=_check_cursor(data,rank,digest)
    previous,_=private.get_content(point["last_file"])
    if previous is None or sha256(previous).hexdigest()!=point["last_file_sha256"]:
        raise DARTEventBatchError("previous_dart_evidence_missing_or_changed")
    if point["next_issuer_index"]==MAX_ISSUERS:
        _read_output(previous)
    else:
        match=re.search(r"_(\d{3})_(\d{3})\.json\.gz$",point["last_file"])
        if not match or int(match.group(2))!=point["next_issuer_index"]:
            raise DARTEventBatchError("prior_dart_batch_filename_invalid")
        _check_report(previous,int(match.group(1)),int(match.group(2)))
    start=point["next_issuer_index"]
    end=min(start+MAX_NEXT_ISSUERS,len(rank))
    if start==end:
        return {"status":"all_flagged_issuers_have_filing_research",
                "issuer_count":start,"dart_provider_calls":0}
    path=_batch_file(start,end)
    existing,_=private.get_content(path)
    if existing is not None:
        evidence=_check_report(existing,start,end)
        blob=existing
        calls=0
        new=False
    else:
        try: mapping=dart.corp_mapping()
        except Exception:raise DARTEventBatchError("official_dart_mapping_unavailable") from None
        results=[]
        for code in rank[start:end]:
            if code not in mapping:
                results.append({"issuer_code":code,"status":"unknown_historical_corporation_mapping",
                                "historical_identity_verified":False})
            else:
                row=_one_issuer(dart,mapping[code],code)
                row["status"]="filing_receipt_research_only"
                results.append(row)
        calls=len(getattr(dart,"research_request_timings",[]))
        if not 1<=calls<=1+(end-start)*2:
            raise DARTEventBatchError("dart_provider_budget_not_verified")
        evidence={"schema":SCHEMA,"first_issuer_index":start,
                  "next_issuer_index":end,"risk_baseline_end":"2016-12-16",
                  "issuer_results":results,"source_dart_calls":calls,
                  "source_krx_calls":0,"original_dart_filing_rows_saved":False,
                  "corporate_actions_certified":False,"v3_scores_certified":False,
                  "filing_category_count":sum(len(r.get("relevant_corporate_action_filing_receipts",[]))
                                              for r in results)}
        blob=gzip.compress(_canonical(evidence),compresslevel=9,mtime=0)
        if len(blob)>=MAX_BYTES:raise DARTEventBatchError("dart_research_batch_oversized")
        private.put_new(path,blob,"Store private bounded historic DART filing receipts")
        _readback_with_retry(private,path,blob)
        new=True
    next_state={**point,"next_issuer_index":end,
                "last_file":path,"last_file_sha256":sha256(blob).hexdigest(),
                "total_disclosure_categories":point["total_disclosure_categories"]+evidence["filing_category_count"]}
    _save_cursor(private,next_state,sha)
    return {"status":"private_dart_historical_receipt_batch_checked",
            "total_historical_risk_issuers":len(rank),
            "completed_issuer_filing_research":end,
            "new_issuers_checked":end-start,
            "filing_categories_in_batch":evidence["filing_category_count"],
            "cumulative_filing_categories":next_state["total_disclosure_categories"],
            "dart_provider_calls":calls,
            "new_private_derived_evidence_files":int(new),
            "raw_price_stored":0,"corporate_actions_certified":False}


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()
        or not os.getenv("DART_API_KEY","").strip()):
        print('{"status":"blocked","reason":"private_dart_secrets_not_configured"}')
        return 1
    try:
        private=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                  os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        dart=DARTClient(os.environ["DART_API_KEY"])
        output=run(private=private,dart=dart)
        print(json.dumps(output,sort_keys=True))
        return 0
    except (DARTEventBatchError,EventEvidenceError) as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),
                          "original_source_logged":False}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"private_or_dart_provider_unavailable","original_source_logged":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
