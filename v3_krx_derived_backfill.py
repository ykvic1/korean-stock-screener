"""Resume-safe official KRX -> private derived-only 10-year research backfill.

Uses five historical observed-session candidates per cohort, 2 KRX EOD
market API reads each day. Original response bytes are never persisted.
One short private checkpoint advances AFTER all transformed files are checked.
"""
from __future__ import annotations

import base64
from datetime import date,datetime,time
from zoneinfo import ZoneInfo
from hashlib import sha256
import json
import os
import time as retry_clock

from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError
from official_probe.clients import KRXProbeError
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_session_candidates import candidate_dates
from v3_krx_derived_5day import (
    store_five_day_factors, validate_derived_blob, DerivedSourceError)

SCHEMA="v3-krx-private-derived-backfill-checkpoint-v1"
START="2016-10-17"
END="2026-10-16"
CHECKPOINT="derived/historical-checkpoint.json"
MAX_WINDOWS_PER_RUN=16
MAX_DERIVED_BYTES=80*1024*1024  # abort before any large private Git growth
MAX_SOURCE_CALLS=MAX_WINDOWS_PER_RUN*10


class DerivedBackfillError(ValueError):
    """No raw source, token, URL or vendor messages in diagnostic text."""


def history_candidates():
    days=calendar_weekday_candidates(START,END)
    cal=candidate_dates(days,source="xkrx")
    return cal["dates"],cal["candidate_dates_sha256"]


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),
                      ensure_ascii=True,allow_nan=False).encode("ascii")


def _empty(checksum):
    return {
        "schema":SCHEMA,"start":START,"end":END,
        "calendar_candidate_hash":checksum,
        "next_window_index":0,"full_issuer_bucket_records":0,
        "compressed_factor_bytes":0,
        "last_window_file_sha256":None,
        "raw_source_file_count":0,
    }


def _manifest(data,dates,checksum):
    obj=_empty(checksum) if data is None else None
    if data is not None:
        try:obj=json.loads(data)
        except Exception:raise DerivedBackfillError("corrupt_derived_history_checkpoint") from None
    max_complete=len(dates)//5
    index=obj.get("next_window_index")
    if (obj.get("schema")!=SCHEMA or obj.get("calendar_candidate_hash")!=checksum
        or obj.get("start")!=START or obj.get("end")!=END
        or type(index) is not int or not 0<=index<=max_complete
        or type(obj.get("full_issuer_bucket_records")) is not int
        or not 0<=obj["full_issuer_bucket_records"]<=25000*max_complete
        or type(obj.get("compressed_factor_bytes")) is not int
        or not 0<=obj["compressed_factor_bytes"]<=MAX_DERIVED_BYTES
        or obj.get("raw_source_file_count")!=0
        or (index==0 and obj.get("last_window_file_sha256") is not None)
        or (index>0 and (type(obj.get("last_window_file_sha256")) is not str
            or len(obj["last_window_file_sha256"])!=64))):
        raise DerivedBackfillError("invalid_derived_history_checkpoint")
    return obj


def _path(days):
    return f"derived/five-session/{days[0]}_{days[-1]}.json.gz"


def _verify_last(storage,checkpoint,dates):
    index=checkpoint["next_window_index"]
    if not index:return
    window=dates[(index-1)*5:index*5]
    blob,_=storage.get_content(_path(window))
    if blob is None or sha256(blob).hexdigest()!=checkpoint["last_window_file_sha256"]:
        raise DerivedBackfillError("previous_private_derived_window_missing_or_mutated")
    validate_derived_blob(blob,window[0],window[-1])


def _save_checkpoint(storage,new,sha):
    payload=_canonical(new)
    if len(payload)>1200:
        raise DerivedBackfillError("oversized_derived_checkpoint")
    if sha is None:
        storage.put_new(CHECKPOINT,payload,"Create compact KRX transformed checkpoint")
    else:
        storage.transport("PUT","contents/"+CHECKPOINT,{
            "message":"Advance compact private derived KRX checkpoint",
            "content":base64.b64encode(payload).decode("ascii"),"sha":sha})
    # GitHub Contents PUT can succeed before the new immutable content is
    # visible on GET. Retry ONLY reading; never repeat checkpoint PUT or KRX
    # requests. Exact bytes matter: a stale/tampered read is never success.
    for attempt in range(4):
        try:
            observed,observed_sha=storage.get_content(CHECKPOINT)
        except PrivateHistoryError:
            observed,observed_sha=None,None
        if observed==payload and type(observed_sha) is str and observed_sha:
            return observed_sha
        if attempt<3:
            retry_clock.sleep(.7*(attempt+1))
    raise DerivedBackfillError("derived_checkpoint_readback_failed")


def collect(*,storage,client,dates,checksum,windows=1,progress=None):
    if (storage.repository!="ykvic1/korean-stock-private-data"
        or type(dates) is not list or dates!=sorted(set(dates))
        or not 5<=len(dates)<=6000 or type(windows) is not int
        or not 1<=windows<=MAX_WINDOWS_PER_RUN
        or type(checksum) is not str or len(checksum)!=64):
        raise DerivedBackfillError("invalid_derived_backfill_inputs")
    storage.verify_private()
    saved,sha=storage.get_content(CHECKPOINT)
    checkpoint=_manifest(saved,dates,checksum)
    _verify_last(storage,checkpoint,dates)
    next_index=checkpoint["next_window_index"]
    end=min(next_index+windows,len(dates)//5)
    calls=0;adopted=0
    # The checkpoint is committed AFTER EACH fully verified immutable window.
    # An unavailable provider day in a later window must never strand already
    # completed earlier windows behind a batch-level final checkpoint.
    checkpoint_sha=sha
    for index in range(next_index,end):
        window=dates[5*index:5*(index+1)]
        if progress:progress("derived_window_"+str(index))
        observed,_=storage.get_content(_path(window))
        if observed is not None:
            known=validate_derived_blob(observed,window[0],window[-1])
            records=known["complete_five_session_issuer_market_pairs"]
            window_bytes=len(observed)
            adopted+=1
            last=sha256(observed).hexdigest()
        else:
            if checkpoint["compressed_factor_bytes"]+500000>MAX_DERIVED_BYTES:
                raise DerivedBackfillError("private_derived_history_budget_limit")
            result=store_five_day_factors(storage,client,window)
            if result["status"]!="persisted_and_readback_verified_derived":
                raise DerivedBackfillError("unexpected_derived_window_state")
            calls+=result["source_api_calls"]
            records=result["complete_issuer_pairs"]
            window_bytes=result["derived_file_bytes"]
            verified,_=storage.get_content(_path(window))
            if verified is None or len(verified)!=window_bytes:
                raise DerivedBackfillError("recent_private_derived_window_not_read_back")
            last=sha256(verified).hexdigest()
        if checkpoint["compressed_factor_bytes"]+window_bytes>MAX_DERIVED_BYTES:
            raise DerivedBackfillError("private_derived_history_budget_limit")
        updated={
            "schema":SCHEMA,"start":START,"end":END,
            "calendar_candidate_hash":checksum,
            "next_window_index":index+1,
            "full_issuer_bucket_records":checkpoint["full_issuer_bucket_records"]+records,
            "compressed_factor_bytes":checkpoint["compressed_factor_bytes"]+window_bytes,
            "last_window_file_sha256":last,
            "raw_source_file_count":0,
        }
        # CAS, exact readback and SHA from this successful commit become
        # the only authority for the NEXT checkpoint advance. Never forge a
        # checkpoint over a failed source or overwrite a mutated private blob.
        checkpoint_sha=_save_checkpoint(storage,updated,checkpoint_sha)
        checkpoint=updated
     return {
        "status":"private_derived_history_checkpoint_verified",
        "completed_five_session_cohorts":checkpoint["next_window_index"],
        "historical_session_candidates_covered":5*checkpoint["next_window_index"],
        "source_candidate_sessions":len(dates),
        "historical_cohorts_remaining":len(dates)//5-checkpoint["next_window_index"],
        "current_run_api_calls":calls,
        "current_run_resumed_existing_windows":adopted,
        "cumulative_stock_cohort_factor_records":checkpoint["full_issuer_bucket_records"],
        "cumulative_private_derived_bytes":checkpoint["compressed_factor_bytes"],
        "private_raw_source_files":0,
        "verified_20_60_120_250_day_factors":False,
        "full_v3_score_certified":False,
    }


def _safe_source_failure_diagnostics(err):
    """Publish fixed KRX failure context, never vendor response or identity."""
    if str(err)!="official_market_window_or_validation_failed":
        return {}
    phase=getattr(err,"safe_failure_phase",None)
    if phase not in ("transport","source_validation","issuer_identity"):
        return {}
    report={"opaque_failure_phase":phase}
    market=getattr(err,"safe_source_market",None)
    day=getattr(err,"safe_source_day",None)
    calls=getattr(err,"safe_completed_market_calls",None)
    if market in ("KOSPI","KOSDAQ") and type(day) is str:
        try:
            valid=date.fromisoformat(day)
        except ValueError:
            valid=None
        if (valid is not None and valid.isoformat()==day
            and START<=day<=END):
            report["failed_source_market"]=market
            report["failed_source_day"]=day
    if type(calls) is int and 0<=calls<10:
        report["fully_validated_market_calls_before_failure"]=calls
    reason=getattr(err,"safe_krx_reason",None)
    if phase=="transport" and type(reason) is str and reason in KRXProbeError.REASONS:
        report["krx_allowlisted_failure_reason"]=reason
        code=getattr(err,"safe_krx_http_status",None)
        if type(code) is int and 100<=code<=599:
            report["http_status_only"]=code
    return report


def main():
    # No extra approval variable for the user's explicit derived-only project
    # request; old raw KRX workflow retains its separate written-license gate.
    if (not os.getenv("KRX_API_KEY","").strip()
        or os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()):
        print('{"status":"blocked","reason":"derived_secret_or_provider_not_ready"}')
        return 1
    stage="initialization"
    try:
        from official_probe.clients import KRXClient
        stage="calendar"
        dates,checksum=history_candidates()
        stage="private_repository"
        private=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                  os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        windows=int(os.getenv("V3_DERIVED_BATCH_WINDOWS","1"))
        stage="collection_or_checkpoint"
        result=collect(storage=private,
                       client=KRXClient(os.environ["KRX_API_KEY"]),
                       dates=dates,checksum=checksum,windows=windows)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (DerivedBackfillError,DerivedSourceError) as err:
        # Operator-only fixed stage codes; never log upstream exception text.
        report={"status":"blocked","category":str(err),
                "source_payload_logged":False}
        report.update(_safe_source_failure_diagnostics(err))
        print(json.dumps(report,sort_keys=True))
        return 1
    except Exception as exc:
        # Class name and stage only; never print provider responses, paths, keys,
        # HTTP errors, or raw data that could disclose licensed market source.
        allowed={"TimeoutError","ConnectionError","HTTPError","PrivateHistoryError",
                 "OSError","KeyError","ValueError","RuntimeError"}
        kind=type(exc).__name__
        if kind not in allowed:kind="other_untrusted_error"
        print(json.dumps({"status":"blocked","category":"untrusted_source_or_private_archive_error",
                          "stage":stage,"exception_type":kind,"source_payload_logged":False}))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
