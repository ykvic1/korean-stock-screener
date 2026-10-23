"""Append-only incremental price-discontinuity risk screening for KRX derivatives.

Bootstrap from REAL already verified 9-cohort private screen (2016-12-16).
Afterward read only NEW five-session buckets and their corresponding bridges,
write one tiny private immutable delta and advance a compact checkpoint.
Read-only OpenDART/KRX API usage 0. A price jump ≠ corporate event evidence.
"""
from __future__ import annotations

from hashlib import sha256
import base64
import gzip
import json
import math
import os
import re

from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_derived_backfill import (
    history_candidates, _manifest, CHECKPOINT as HISTORY_PATH, _path)
from v3_krx_derived_5day import validate_derived_blob
from v3_krx_derived_bridges import (
    _read_link, _link_path, _check_progress as bridge_progress,
    CHECKPOINT as BRIDGES_PATH, _readback_with_retry)
from v3_krx_derived_corporate_action_risk import (
    _read_existing as baseline_read, _file_end as baseline_path,
    BUCKET_JUMP_BP, BOUNDARY_ABS_LOG_E9)

SCHEMA="v3-private-krx-incremental-price-risk-v1"
CHECKPOINT="derived/quality/incremental-checkpoint.json"
BASELINE_COHORTS=9
# Process one full validated 16-cohort boundary batch at a time. The source
# backfill and cross-bucket linking each run three times per day with this cap.
# This reviewer makes ZERO KRX/DART provider calls; all inputs remain private.
MAX_DELTA_COHORTS=16
MAX_FILE_BYTES=200000
MAX_ISSUE_RECORDS=15000
MAX_CUMULATIVE_BYTES=30*1024*1024
CODE=re.compile(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}\Z")


class IncrementalRiskError(ValueError):
    """No third-party text, source prices or access credentials in errors."""


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),
                      ensure_ascii=True,allow_nan=False).encode("ascii")


def _delta_path(dates,start,end):
    return f"derived/quality/batches/{dates[5*start]}_{dates[5*end-1]}.json.gz"


def _check_progress(data,dates,digest,source_available):
    if data is None:
        return None
    try:
        item=json.loads(data)
        cursor=item["next_cohort_index"]
        if (item.get("schema")!=SCHEMA
            or item.get("calendar_candidate_hash")!=digest
            or type(cursor) is not int
            or not BASELINE_COHORTS<=cursor<=source_available
            or item.get("raw_provider_files_stored")!=0
            or type(item.get("cumulative_price_discontinuity_flags")) is not int
            or item["cumulative_price_discontinuity_flags"]<0
            or type(item.get("cumulative_issuer_buckets_examined")) is not int
            or item["cumulative_issuer_buckets_examined"]<0
            or type(item.get("cumulative_issuer_bridges_examined")) is not int
            or item["cumulative_issuer_bridges_examined"]<0
            or type(item.get("cumulative_quality_compressed_bytes")) is not int
            or not 0<item["cumulative_quality_compressed_bytes"]<MAX_CUMULATIVE_BYTES
            or type(item.get("last_audit_file")) is not str
            or not item["last_audit_file"].startswith("derived/quality/")
            or type(item.get("last_audit_sha256")) is not str
            or not re.fullmatch(r"[a-f0-9]{64}",item["last_audit_sha256"])
            or item.get("historical_candidate_last_day")!=dates[5*cursor-1]):
            raise ValueError()
        return item
    except Exception:
        raise IncrementalRiskError("invalid_private_incremental_quality_checkpoint") from None


def _validate_delta(raw,*,days,start,end):
    """Fail closed on falsified private anomaly event dates, counts and magnitudes.

    These checks prove ONLY event-structure consistency, never an official
    corporate action or a historical split-adjusted price.
    """
    try:
        if (type(raw) is not bytes or not 0<len(raw)<MAX_FILE_BYTES
            or type(days) is not list or days!=sorted(set(days))
            or type(start) is not int or type(end) is not int
            or not BASELINE_COHORTS<=start<end<=len(days)//5):
            raise ValueError()
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            body=stream.read(4_000_001)
        if len(body)>4_000_000:
            raise ValueError()
        item=json.loads(body)
        flags=item.get("flags")
        observed_buckets=item.get("issuer_bucket_factors_examined")
        observed_links=item.get("issuer_cross_boundary_factors_examined")
        count=item.get("flag_count")
        if (item.get("schema")!=SCHEMA
            or item.get("first_cohort_index")!=start
            or item.get("next_cohort_index")!=end
            or item.get("first_day")!=days[5*start]
            or item.get("last_day")!=days[5*end-1]
            or item.get("provider_api_calls")!=0
            or item.get("original_ohlcv_stored") is not False
            or item.get("corporate_action_certified") is not False
            or item.get("v3_trade_score_certified") is not False
            or type(observed_buckets) is not int or observed_buckets<0
            or type(observed_links) is not int or observed_links<0
            or type(count) is not int or count<0
            or type(flags) is not list or len(flags)!=count
            or count>MAX_ISSUE_RECORDS
            or count>observed_buckets+observed_links):
            raise ValueError()
        # Every heuristic issue MUST correspond to exactly one of the
        # verified five-session windows or its immediately preceding bridge.
        # The first bridge belongs to this chunk even if its older endpoint
        # precedes the chunk's first day.
        allowed=set()
        for index in range(start,end):
            first,last=days[5*index],days[5*index+4]
            allowed.add(("four_interval_change",first,last))
            if index:
                allowed.add(("cohort_boundary_change",days[5*index-1],first))
        seen=set()
        for flag in flags:
            if not isinstance(flag,dict):
                raise ValueError()
            code=flag.get("issuer_market")
            kind=flag.get("type")
            first,last=flag.get("first_day"),flag.get("last_day")
            bp=flag.get("return_bp")
            identity=(kind,first,last,code)
            if (type(code) is not str or not CODE.fullmatch(code)
                or (kind,first,last) not in allowed or identity in seen
                or type(bp) is not int or not -10000<bp<=100_000_000):
                raise ValueError()
            if kind=="four_interval_change" and abs(bp)<BUCKET_JUMP_BP:
                raise ValueError()
            # Quantization rounds the actual bridge return to 1 bp; allow
            # at most one bp tolerance against the original log threshold.
            if (kind=="cohort_boundary_change" and
                abs(math.log1p(bp/10000))+0.0001<BOUNDARY_ABS_LOG_E9/1e9):
                raise ValueError()
            seen.add(identity)
        return item
    except Exception:
        raise IncrementalRiskError("invalid_immutable_incremental_quality_chunk") from None


def _save_checkpoint(storage,payload,sha):
    encoded=_canonical(payload)
    if len(encoded)>1200:
        raise IncrementalRiskError("oversized_private_quality_checkpoint")
    if sha is None:
        storage.put_new(CHECKPOINT,encoded,"Create small private KRX action-risk cursor")
    else:
        storage.transport("PUT","contents/"+CHECKPOINT,{
            "message":"Advance private KRX incremental discontinuity cursor",
            "content":base64.b64encode(encoded).decode("ascii"),
            "sha":sha})
    _readback_with_retry(storage,CHECKPOINT,encoded)


def run(*,storage,days,digest,limit=MAX_DELTA_COHORTS):
    if (storage.repository!="ykvic1/korean-stock-private-data"
        or type(days) is not list or days!=sorted(set(days))
        or not 45<=len(days)<=6000
        or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}",digest)
        or type(limit) is not int or not 1<=limit<=MAX_DELTA_COHORTS):
        raise IncrementalRiskError("invalid_private_incremental_quality_request")
    storage.verify_private()
    original,_=storage.get_content(HISTORY_PATH)
    if original is None:
        raise IncrementalRiskError("missing_verified_historical_source_cursor")
    try:
        source=_manifest(original,days,digest)
    except Exception:
        raise IncrementalRiskError("invalid_verified_historical_source_cursor") from None
    available=source["next_window_index"]
    if available<BASELINE_COHORTS:
        return {"status":"awaiting_real_baseline_cohorts","available":available,
                "official_api_calls":0,"raw_source_files_stored":0}
    bridge_raw,_=storage.get_content(BRIDGES_PATH)
    if bridge_raw is None:
        raise IncrementalRiskError("missing_bridge_quality_cursor")
    try:
        linked=bridge_progress(bridge_raw,days,digest,available)["next_boundary_index"]
    except Exception:
        raise IncrementalRiskError("invalid_bridge_quality_cursor") from None
    # The bridge cursor counts the NEXT boundary index. Only complete cohorts
    # with all 8+ necessary bridge links may enter the quality screen.
    eligible=min(available,linked)
    if eligible<BASELINE_COHORTS:
        return {"status":"awaiting_cross_cohort_risk_links","available":available,
                "bridge_cohorts":eligible,"official_api_calls":0}
    state_raw,sha=storage.get_content(CHECKPOINT)
    state=_check_progress(state_raw,days,digest,available)
    if state is None:
        seedfile=baseline_path(days,BASELINE_COHORTS)
        saved,_=storage.get_content(seedfile)
        if saved is None:
            raise IncrementalRiskError("missing_verified_nine_cohort_baseline")
        seed=baseline_read(saved,days[5*BASELINE_COHORTS-1],BASELINE_COHORTS)
        if seed.get("historical_calendar_candidate_hash")!=digest:
            raise IncrementalRiskError("baseline_calendar_not_authentic")
        if seed.get("reviewed_cross_bucket_links")!=8:
            raise IncrementalRiskError("baseline_bridges_not_complete")
        state={
            "schema":SCHEMA,
            "calendar_candidate_hash":digest,
            "next_cohort_index":BASELINE_COHORTS,
            "historical_candidate_last_day":days[5*BASELINE_COHORTS-1],
            "cumulative_price_discontinuity_flags":seed["total_flags"],
            "cumulative_issuer_buckets_examined":seed["observed_issuer_bucket_factors"],
            "cumulative_issuer_bridges_examined":seed["observed_issuer_boundary_links"],
            "cumulative_quality_compressed_bytes":len(saved),
            "last_audit_file":seedfile,
            "last_audit_sha256":sha256(saved).hexdigest(),
            "raw_provider_files_stored":0,
        }
        _save_checkpoint(storage,state,None)
        return {"status":"verified_baseline_adopted_without_provider_requests",
                "cohorts_examined":BASELINE_COHORTS,
                "cumulative_flags":state["cumulative_price_discontinuity_flags"],
                "source_provider_calls":0,"new_quality_files":0}
    check,_=storage.get_content(state["last_audit_file"])
    if check is None or sha256(check).hexdigest()!=state["last_audit_sha256"]:
        raise IncrementalRiskError("previous_private_quality_chunk_missing_or_tampered")
    if state["next_cohort_index"]==BASELINE_COHORTS:
        baseline_read(check,days[5*BASELINE_COHORTS-1],BASELINE_COHORTS)
    elif state["next_cohort_index"]>BASELINE_COHORTS:
        previous_end=state["next_cohort_index"]
        previous_start=previous_end-1
        # The checkpoint path owns the immutable start; infer from filename.
        suffix=state["last_audit_file"].split("/")[-1].removesuffix(".json.gz")
        parts=suffix.split("_")
        if len(parts)!=2:
            raise IncrementalRiskError("invalid_prior_quality_chunk_path")
        _validate_delta(check,days=days,
                        start=days.index(parts[0])//5,
                        end=previous_end)
    start=state["next_cohort_index"]
    end=min(start+limit,eligible)
    if start>=end:
        return {"status":"waiting_for_new_verified_derived_cohorts",
                "cohorts_examined":start,"available_cohorts":available,
                "verified_link_cohorts":eligible,
                "cumulative_flags":state["cumulative_price_discontinuity_flags"],
                "source_provider_calls":0,"new_quality_files":0}
    path=_delta_path(days,start,end)
    old,_=storage.get_content(path)
    if old is not None:
        result=_validate_delta(old,days=days,start=start,end=end)
        newfile=False
        blob=old
    else:
        flags=[]
        inspected_buckets=0
        inspected_bridges=0
        for index in range(start,end):
            period=days[5*index:5*(index+1)]
            raw,_=storage.get_content(_path(period))
            if raw is None:
                raise IncrementalRiskError("missing_five_session_research_evidence")
            try:
                factor=validate_derived_blob(raw,period[0],period[-1])
            except Exception:
                raise IncrementalRiskError("invalid_five_session_research_evidence") from None
            for code,v in factor["derived_issuer_factors"].items():
                inspected_buckets+=1
                r=v["intrabucket_four_interval_return_bp"]
                if type(r) is not int or not CODE.fullmatch(code):
                    raise IncrementalRiskError("invalid_issuer_derived_change")
                if abs(r)>=BUCKET_JUMP_BP:
                    flags.append({"issuer_market":code,
                                  "first_day":period[0],"last_day":period[-1],
                                  "type":"four_interval_change","return_bp":r})
            previous=days[5*index-1]
            newer=period[0]
            raw_link,_=storage.get_content(_link_path(previous,newer))
            if raw_link is None:
                raise IncrementalRiskError("missing_source_cross_cohort_link")
            try:
                link=_read_link(raw_link,previous,newer)
            except Exception:
                raise IncrementalRiskError("invalid_source_cross_cohort_link") from None
            for code,v in link["derived_logreturn_by_market_issuer"].items():
                inspected_bridges+=1
                logret=v["cross_bucket_log_return_e9"]
                if type(logret) is not int or not CODE.fullmatch(code):
                    raise IncrementalRiskError("invalid_derived_boundary_change")
                if abs(logret)>=BOUNDARY_ABS_LOG_E9:
                    flags.append({"issuer_market":code,
                                  "first_day":previous,"last_day":newer,
                                  "type":"cohort_boundary_change",
                                  "return_bp":round(math.expm1(logret/1e9)*10000)})
            if len(flags)>MAX_ISSUE_RECORDS:
                raise IncrementalRiskError("too_many_incremental_price_risk_flags")
        result={
            "schema":SCHEMA,"first_cohort_index":start,
            "next_cohort_index":end,
            "first_day":days[5*start],"last_day":days[5*end-1],
            "issuer_bucket_factors_examined":inspected_buckets,
            "issuer_cross_boundary_factors_examined":inspected_bridges,
            "flag_count":len(flags),"flags":flags,
            "provider_api_calls":0,"original_ohlcv_stored":False,
            "corporate_action_certified":False,
            "v3_trade_score_certified":False,
        }
        blob=gzip.compress(_canonical(result),compresslevel=9,mtime=0)
        if not 0<len(blob)<MAX_FILE_BYTES:
            raise IncrementalRiskError("private_risk_delta_above_limit")
        if state["cumulative_quality_compressed_bytes"]+len(blob)>=MAX_CUMULATIVE_BYTES:
            raise IncrementalRiskError("private_quality_budget_limit")
        storage.put_new(path,blob,"Write private derived-only KRX action-risk chunk")
        _readback_with_retry(storage,path,blob)
        newfile=True
    progress={
        "schema":SCHEMA,"calendar_candidate_hash":digest,
        "next_cohort_index":end,
        "historical_candidate_last_day":days[5*end-1],
        "cumulative_price_discontinuity_flags":
            state["cumulative_price_discontinuity_flags"]+result["flag_count"],
        "cumulative_issuer_buckets_examined":
            state["cumulative_issuer_buckets_examined"]+result["issuer_bucket_factors_examined"],
        "cumulative_issuer_bridges_examined":
            state["cumulative_issuer_bridges_examined"]+result["issuer_cross_boundary_factors_examined"],
        "cumulative_quality_compressed_bytes":
            state["cumulative_quality_compressed_bytes"]+len(blob),
        "last_audit_file":path,"last_audit_sha256":sha256(blob).hexdigest(),
        "raw_provider_files_stored":0,
    }
    _save_checkpoint(storage,progress,sha)
    return {
        "status":"incremental_price_discontinuity_screen_saved",
        "new_verified_cohorts_examined":end-start,
        "completed_quality_cohorts":end,
        "new_flags":result["flag_count"],
        "cumulative_flags":progress["cumulative_price_discontinuity_flags"],
        "new_quality_files":int(newfile),
        "quality_evidence_files_reused":int(not newfile),
        "private_quality_cumulative_bytes":progress["cumulative_quality_compressed_bytes"],
        "source_provider_calls":0,"raw_prices_persisted":0,
        "corporate_events_verified":False,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()):
        print('{"status":"blocked","reason":"private_quality_credentials_missing"}')
        return 1
    try:
        dates,digest=history_candidates()
        storage=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                  os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        report=run(storage=storage,days=dates,digest=digest)
        print(json.dumps(report,sort_keys=True))
        return 0
    except IncrementalRiskError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),
                          "no_provider_source_published":True},sort_keys=True))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"private_derived_validation_or_transport_failed","no_provider_source_published":true}')
        return 1

if __name__=="__main__":
    raise SystemExit(main())
