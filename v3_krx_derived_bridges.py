"""KRX transformed-only historical cross-cohort boundary return research.

Every existing five-session bucket includes four price-change intervals; it
does not include the boundary interval to the next bucket. To prevent falsely
joining those buckets into exact 20/60/120/250-session returns, read the TWO
adjacent historical source days in memory, derive only log-return and quality
counts, and store that derived link in a private immutable file. Source closes
and entire vendor market rows are discarded after processing.
"""
from __future__ import annotations

from hashlib import sha256
import base64
import gzip
import json
import math
import os
import re
import time

from v3_krx_derived_backfill import (history_candidates, CHECKPOINT as HISTORY_CHECKPOINT,
                                     _manifest as _history_manifest, DerivedBackfillError)
from v3_krx_derived_5day import validate_derived_blob
from v3_krx_history_collection import _snapshot_market
from v3_krx_daily_anomaly_triage import classify_row
from official_probe.validation import _numeric
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA="v3-krx-private-cohort-boundary-log-return-v1"
CHECKPOINT="derived/boundaries-checkpoint.json"
# Match one scheduled historical source batch (16 five-session cohorts).
# At most 64 official calls per invocation: 16 links x 2 days x 2 markets.
MAX_LINKS_PER_RUN=16
MAX_BLOB_BYTES=260_000
MAX_TOTAL_BYTES=40*1024*1024


class BoundaryError(ValueError):
    """Safe fixed diagnostic strings only; never raise vendor HTTP content."""


def _canonical(item):
    return json.dumps(item,sort_keys=True,ensure_ascii=True,separators=(",",":"),
                      allow_nan=False).encode("ascii")


def _source_closes(client,market,day):
    try:
        rows,raw=client.daily_market(market,day)
        inspected=_snapshot_market(rows,raw,market=market,asof=day)
        observed={}
        for row in rows:
            if classify_row(row)[0]!="valid_numeric_observation":
                continue
            code=row["ISU_CD"]
            close=float(_numeric(row["TDD_CLSPRC"],"TDD_CLSPRC"))
            if math.isfinite(close) and close>0:
                observed[code]=close
        return observed,{
            "provider_rows":inspected["provider_rows_observed"],
            "usable_rows":inspected["observed_numeric_candidates"],
            "quarantined_rows":inspected["quarantined_unpriced_candidates"],
        }
    except Exception:
        raise BoundaryError("official_boundary_session_unavailable_or_invalid") from None


def derive_link(client,previous_end,next_start):
    """Exactly four KRX API market calls; no source prices in return object."""
    if (type(previous_end) is not str or type(next_start) is not str
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",previous_end)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",next_start)
        or previous_end>=next_start):
        raise BoundaryError("invalid_derived_boundary_dates")
    links={};coverage={}
    for market in ("KOSPI","KOSDAQ"):
        older,meta_old=_source_closes(client,market,previous_end)
        newer,meta_new=_source_closes(client,market,next_start)
        coverage[market]={"previous":meta_old,"next":meta_new,
                          "both_date_price_observations":len(set(older)&set(newer))}
        for code in sorted(set(older)&set(newer)):
            ratio=newer[code]/older[code]
            if not math.isfinite(ratio) or ratio<=0:
                continue
            log_ret=math.log(ratio)
            if not math.isfinite(log_ret) or abs(log_ret)>10:
                continue
            links[f"{market}:{code}"]={
                "cross_bucket_log_return_e9":round(log_ret*1_000_000_000),
                "issuer_identity_across_corporate_actions_verified":False,
                "corporate_action_adjusted":False,
                "source_absolute_closes_stored":False,
            }
    result={
        "schema":SCHEMA,"previous_bucket_last_day":previous_end,
        "next_bucket_first_day":next_start,
        "source_api_calls":4,
        "market_quality_counts":coverage,
        "observed_boundary_issuer_pairs":len(links),
        "derived_logreturn_by_market_issuer":links,
        "vendor_raw_source_stored":False,
        "absolute_closes_or_volumes_stored":False,
        "corporate_actions_adjusted":False,
        "historic_issuer_identity_reconciled":False,
        "individual_20_60_120_250_session_returns_certified":False,
        "for_private_noncommercial_research_only":True,
    }
    blob=gzip.compress(_canonical(result),compresslevel=9,mtime=0)
    if not 0<len(blob)<MAX_BLOB_BYTES:
        raise BoundaryError("derived_boundary_blob_size_invalid")
    return result,blob


def _read_link(blob,prev_day,next_day):
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_BLOB_BYTES:
            raise ValueError()
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as z:
            raw=z.read(12_000_001)
        if len(raw)>12_000_000:
            raise ValueError()
        x=json.loads(raw)
        if (x.get("schema")!=SCHEMA or x.get("previous_bucket_last_day")!=prev_day
            or x.get("next_bucket_first_day")!=next_day
            or x.get("source_api_calls")!=4
            or x.get("vendor_raw_source_stored") is not False
            or x.get("absolute_closes_or_volumes_stored") is not False
            or x.get("corporate_actions_adjusted") is not False
            or x.get("individual_20_60_120_250_session_returns_certified") is not False):
            raise ValueError()
        observed=x["derived_logreturn_by_market_issuer"]
        if (type(observed) is not dict
            or len(observed)!=x["observed_boundary_issuer_pairs"]):
            raise ValueError()
        for k,v in observed.items():
            if (type(k) is not str
                or not re.fullmatch(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}",k)
                or type(v.get("cross_bucket_log_return_e9")) is not int
                or abs(v["cross_bucket_log_return_e9"])>10_000_000_000
                or set(v)!={"cross_bucket_log_return_e9",
                             "issuer_identity_across_corporate_actions_verified",
                             "corporate_action_adjusted",
                             "source_absolute_closes_stored"}
                or v["source_absolute_closes_stored"] is not False):
                raise ValueError()
        if any(token in raw for token in (b'"TDD_CLSPRC"',b'"ACC_TRDVOL"',
                                           b'"raw_source_base64"')):
            raise ValueError()
        return x
    except Exception:
        raise BoundaryError("private_existing_boundary_derived_invalid") from None


def _readback_with_retry(storage,path,expected):
    """GitHub's new Contents blob may be briefly stale after a successful PUT.

    Never repeat the PUT or official provider calls; instead verify the saved
    immutable bytes with a finite 4-read retry window and fail closed.
    """
    for attempt in range(4):
        observed,_=storage.get_content(path)
        if observed==expected:
            return True
        if attempt<3:
            time.sleep(0.7*(attempt+1))
    raise BoundaryError("private_derived_write_not_visible_after_retry")


def _link_path(older,newer):
    return f"derived/boundaries/{older}_{newer}.json.gz"


def _check_progress(data,dates,calendar_hash,completed):
    empty={
        "schema":"v3-krx-private-boundary-progress-v1",
        "calendar_candidate_hash":calendar_hash,
        "next_boundary_index":1,
        "cumulative_derived_boundary_issuer_records":0,
        "cumulative_compressed_bytes":0,
        "last_boundary_sha256":None,
        "original_source_files_stored":0,
    }
    if data is None:
        return empty
    try: obj=json.loads(data)
    except Exception:
        raise BoundaryError("invalid_derived_boundary_checkpoint") from None
    n=obj.get("next_boundary_index")
    if (obj.get("schema")!=empty["schema"]
        or obj.get("calendar_candidate_hash")!=calendar_hash
        or type(n) is not int or not 1<=n<=completed
        or type(obj.get("cumulative_derived_boundary_issuer_records")) is not int
        or obj["cumulative_derived_boundary_issuer_records"]<0
        or type(obj.get("cumulative_compressed_bytes")) is not int
        or not 0<=obj["cumulative_compressed_bytes"]<=MAX_TOTAL_BYTES
        or obj.get("original_source_files_stored")!=0
        or (n==1 and obj.get("last_boundary_sha256") is not None)
        or (n>1 and (type(obj.get("last_boundary_sha256")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}",obj["last_boundary_sha256"])))):
        raise BoundaryError("invalid_derived_boundary_checkpoint")
    return obj


def run(*,storage,client,dates,calendar_hash,max_links=MAX_LINKS_PER_RUN):
    """Link only fully materialized five-session private cohorts (no raw)."""
    if (storage.repository!="ykvic1/korean-stock-private-data"
        or type(dates) is not list or dates!=sorted(set(dates))
        or len(dates)<10
        or type(calendar_hash) is not str or len(calendar_hash)!=64
        or type(max_links) is not int or not 1<=max_links<=MAX_LINKS_PER_RUN):
        raise BoundaryError("invalid_boundary_backfill_configuration")
    storage.verify_private()
    data,_=storage.get_content(HISTORY_CHECKPOINT)
    if data is None:
        raise BoundaryError("derived_source_checkpoint_not_available")
    try:
        checkpoint=_history_manifest(data,dates,calendar_hash)
    except DerivedBackfillError:
        raise BoundaryError("historical_derived_source_checkpoint_invalid") from None
    complete=checkpoint["next_window_index"]
    if complete<2:
        return {"status":"waiting_for_second_5session_derived_window",
                "api_calls":0,"completed_boundaries":0,
                "source_cohorts_available":complete}
    cached,checkpoint_sha=storage.get_content(CHECKPOINT)
    progress=_check_progress(cached,dates,calendar_hash,complete)
    pointer=progress["next_boundary_index"]
    if pointer>1:
        older,next_=dates[(pointer-1)*5-1],dates[(pointer-1)*5]
        blob,_=storage.get_content(_link_path(older,next_))
        if blob is None or sha256(blob).hexdigest()!=progress["last_boundary_sha256"]:
            raise BoundaryError("private_previous_derived_boundary_damaged")
        _read_link(blob,older,next_)
    last_end=min(complete,pointer+max_links)
    new_calls=0;resumed=0;count=0;new_bytes=0;last_digest=progress["last_boundary_sha256"]
    for idx in range(pointer,last_end):
        prev,last=dates[idx*5-1],dates[idx*5]
        # The linkage is only valid between TWO real completed and validated
        # derived cohorts. Check the actual immutable factor files, not merely
        # an optimistic checkpoint counter.
        for begin in ((idx-1)*5,idx*5):
            cohort=dates[begin:begin+5]
            factor_path=f"derived/five-session/{cohort[0]}_{cohort[-1]}.json.gz"
            saved_factor,_=storage.get_content(factor_path)
            if saved_factor is None:
                raise BoundaryError("missing_source_derived_cohort_file")
            try:
                validate_derived_blob(saved_factor,cohort[0],cohort[-1])
            except Exception:
                raise BoundaryError("invalid_source_derived_cohort_file") from None
        path=_link_path(prev,last)
        original,_=storage.get_content(path)
        if original is None:
            if progress["cumulative_compressed_bytes"]+new_bytes+MAX_BLOB_BYTES>MAX_TOTAL_BYTES:
                raise BoundaryError("private_derived_boundary_storage_budget_reached")
            fact,blob=derive_link(client,prev,last)
            # Ingest only a derived factor file. Original KRX responses die in RAM.
            storage.put_new(path,blob,"Store private cross-bucket KRX derived "+last)
            _readback_with_retry(storage,path,blob)
            new_calls+=4
        else:
            blob=original
            fact=_read_link(blob,prev,last)
            resumed+=1
        _read_link(blob,prev,last)
        count+=fact["observed_boundary_issuer_pairs"]
        new_bytes+=len(blob)
        last_digest=sha256(blob).hexdigest()
    if last_end>pointer:
        output={
            "schema":"v3-krx-private-boundary-progress-v1",
            "calendar_candidate_hash":calendar_hash,
            "next_boundary_index":last_end,
            "cumulative_derived_boundary_issuer_records":
                progress["cumulative_derived_boundary_issuer_records"]+count,
            "cumulative_compressed_bytes":progress["cumulative_compressed_bytes"]+new_bytes,
            "last_boundary_sha256":last_digest,
            "original_source_files_stored":0,
        }
        raw=_canonical(output)
        if len(raw)>=1000:
            raise BoundaryError("derived_boundary_checkpoint_too_large")
        if checkpoint_sha is None:
            storage.put_new(CHECKPOINT,raw,"Create private derived boundary checkpoint")
        else:
            storage.transport("PUT","contents/"+CHECKPOINT,{
                "message":"Advance private KRX derived boundary checkpoint",
                "content":base64.b64encode(raw).decode("ascii"),
                "sha":checkpoint_sha})
        _readback_with_retry(storage,CHECKPOINT,raw)
        progress=output
    return {
        "status":"derived_krx_bucket_boundaries_stored_and_verified",
        "source_cohorts_available":complete,
        "cumulative_completed_derived_boundaries":progress["next_boundary_index"]-1,
        "cumulative_boundary_issuer_observations":
            progress["cumulative_derived_boundary_issuer_records"],
        "cumulative_derived_bridge_bytes":progress["cumulative_compressed_bytes"],
        "new_market_api_calls":new_calls,
        "existing_boundaries_reused":resumed,
        "raw_provider_files_saved":0,
        "exact_adjusted_20_60_120_250_returns_verified":False,
        "ias20_verified":False,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()
        or not os.getenv("KRX_API_KEY","").strip()):
        print('{"status":"blocked","reason":"private_derived_bridge_missing_credentials"}')
        return 1
    try:
        from official_probe.clients import KRXClient
        days,digest=history_candidates()
        store=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        report=run(storage=store,client=KRXClient(os.environ["KRX_API_KEY"]),
                   dates=days,calendar_hash=digest)
        print(json.dumps(report,sort_keys=True))
        return 0
    except (BoundaryError,ValueError) as exc:
        # All internally raised boundary issues use fixed, data-free messages.
        msg=str(exc) if type(exc) is BoundaryError else "invalid_private_checkpoint"
        print(json.dumps({"status":"blocked","category":msg,"source_exposed":False}))
        return 1
    except Exception:
        print('{"status":"blocked","category":"untrusted_provider_or_private_connection","source_exposed":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
