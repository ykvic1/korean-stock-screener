"""PRIVATE KRX 20/60/120/250-session approximate returns from derived data.

Zero KRX API calls and zero original OHLCV stored. A five-day bucket preserves
one *rounded* four-interval return; the between-bucket logreturn is recorded
separately in a validated private bridge. Compounding gives a RESEARCH ONLY
approximation of raw close-to-close return across N observed trading sessions.
Corporate actions, issuer continuity, PIT availability and adjusted prices are
NOT certified. Never route these estimates into V3 live scores.
"""
from __future__ import annotations

import base64
import gzip
from hashlib import sha256
from io import BytesIO
import json
import math
import os
import re

from v3_krx_derived_5day import validate_derived_blob
from v3_krx_derived_backfill import (
    _calendar_hash_matches, history_candidates, CHECKPOINT as HISTORY_CHECKPOINT,
    _manifest as _history_manifest)
from v3_krx_derived_bridges import (
    CHECKPOINT as BRIDGE_CHECKPOINT,
    _check_progress as _bridge_progress, _link_path, _read_link,
    _readback_with_retry)
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA="v3-krx-private-bucket-composed-unadjusted-return-v1"
CHECKPOINT_SCHEMA="v3-krx-private-rolling-cohort-return-progress-v1"
HORIZONS=(20,60,120,250)
# Process the same bounded 24 newly bridged cohorts per scheduled run.
# Composition uses only immutable private derived evidence, zero KRX calls.
MAX_PER_HORIZON=24
MAX_BLOB=250_000
MAX_HISTORY_BYTES=40*1024*1024


class DerivedReturnError(ValueError):
    """Only static error labels, no supplier or token messages."""


def _json(data):
    return json.dumps(data,sort_keys=True,ensure_ascii=True,
                      separators=(",",":"),allow_nan=False).encode("ascii")


def _file(day0,day1,horizon):
    return f"derived/rolling/{horizon}/{day0}_{day1}.json.gz"


def _progress_path(horizon):
    return f"derived/rolling/{horizon}/checkpoint.json"


def _validate_rolling(blob, horizon, start, end):
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_BLOB:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as z:
            data=z.read(5_000_001)
        if len(data)>5_000_000:
            raise ValueError()
        obj=json.loads(data)
        if (obj.get("schema")!=SCHEMA or obj.get("horizon_observed_sessions")!=horizon
            or obj.get("window_start")!=start or obj.get("window_end")!=end
            or obj.get("total_observed_intervals")!=horizon-1
            or obj.get("vendor_raw_retained") is not False
            or obj.get("source_unadjusted_quantized_approximation") is not True
            or obj.get("full_v3_factor_score_approved") is not False):
            raise ValueError()
        values=obj["issuer_market_derived_returns_bp"]
        if type(values) is not dict or len(values)!=obj["complete_issuer_count"]:
            raise ValueError()
        for code,amount in values.items():
            if (type(code) is not str
                or not re.fullmatch(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}",code)
                or type(amount) is not int or abs(amount)>100_000_000):
                raise ValueError()
        if any(t in data for t in (b'"TDD_CLSPRC"',b'"ACC_TRDVOL"',b'"raw_source_base64"')):
            raise ValueError()
        return obj
    except Exception:
        raise DerivedReturnError("private_derived_composed_return_invalid") from None


def _read_factor(storage,dates,bucket):
    w=dates[5*bucket:5*(bucket+1)]
    filename=f"derived/five-session/{w[0]}_{w[-1]}.json.gz"
    body,_=storage.get_content(filename)
    if body is None:
        raise DerivedReturnError("missing_five_session_derived_input")
    try:
        return validate_derived_blob(body,w[0],w[-1])["derived_issuer_factors"]
    except Exception:
        raise DerivedReturnError("five_session_derived_input_invalid") from None


def _read_bridge(storage,dates,index):
    p,q=dates[5*index-1],dates[5*index]
    blob,_=storage.get_content(_link_path(p,q))
    if blob is None:
        raise DerivedReturnError("missing_boundary_derived_input")
    try:
        return _read_link(blob,p,q)["derived_logreturn_by_market_issuer"]
    except Exception:
        raise DerivedReturnError("boundary_derived_input_invalid") from None


def _estimate_one(storage,dates,horizon,end_bucket, factor_cache, bridge_cache):
    """Exactly H-1 observed internal+bridge close-to-close intervals."""
    buckets=horizon//5
    start_bucket=end_bucket-buckets
    cohort=[]
    for n in range(start_bucket,end_bucket):
        if n not in factor_cache:
            factor_cache[n]=_read_factor(storage,dates,n)
        cohort.append(factor_cache[n])
    bridge=[]
    for n in range(start_bucket+1,end_bucket):
        if n not in bridge_cache:
            bridge_cache[n]=_read_bridge(storage,dates,n)
        bridge.append(bridge_cache[n])
    if len(cohort)!=buckets or len(bridge)!=buckets-1:
        raise DerivedReturnError("incomplete_derived_return_interval_coverage")
    keys=set(cohort[0])
    for x in cohort[1:]:
        keys.intersection_update(x)
    for x in bridge:
        keys.intersection_update(x)
    values={}
    for key in sorted(keys):
        try:
            portions=[]
            for row in cohort:
                observation=row[key]
                amount=observation["intrabucket_four_interval_return_bp"]
                if (type(amount) is not int or not -10000<amount<=1_000_000
                    or observation.get("observed_sessions")!=5
                    or observation.get("release_score_certified") is not False):
                    raise ValueError()
                portions.append(math.log1p(amount/10000))
            for row in bridge:
                amount=row[key]["cross_bucket_log_return_e9"]
                if type(amount) is not int or abs(amount)>10_000_000_000:
                    raise ValueError()
                portions.append(amount/1_000_000_000)
            # 4 internal intervals per bucket + 1 connecting interval
            # between each consecutive bucket => N-1 intervals.
            if len(portions)!=(2*buckets-1):
                raise ValueError()
            estimate=math.expm1(math.fsum(portions))
            if not math.isfinite(estimate) or abs(estimate)>10_000:
                continue
            values[key]=round(estimate*10_000)
        except (ValueError,KeyError,TypeError,OverflowError):
            # Missing or corrupt observations are not silently filled as zero.
            continue
    payload={
        "schema":SCHEMA,
        "window_start":dates[5*start_bucket],
        "window_end":dates[5*end_bucket-1],
        "horizon_observed_sessions":horizon,
        "total_observed_intervals":horizon-1,
        "full_cohorts_required":buckets,
        "cross_cohort_links_required":buckets-1,
        "source_cohort_internal_interval_count":buckets*4,
        "source_cohort_cross_interval_count":buckets-1,
        "complete_issuer_count":len(values),
        "issuer_market_derived_returns_bp":values,
        "vendor_raw_retained":False,
        "stock_identity_issuer_action_verified":False,
        "adjusted_close_prices_verified":False,
        "source_unadjusted_quantized_approximation":True,
        "no_pit_lookahead_certification":True,
        "full_v3_factor_score_approved":False,
        "ias_20_score_approved":False,
        "for_private_noncommercial_research_only":True,
    }
    encoded=gzip.compress(_json(payload),compresslevel=9,mtime=0)
    if not 0<len(encoded)<MAX_BLOB:
        raise DerivedReturnError("derived_composed_return_too_large")
    return payload,encoded


def _check_progress(raw,calendar_hash,horizon):
    expected_first=horizon//5
    if raw is None:
        return {
            "schema":CHECKPOINT_SCHEMA,"horizon":horizon,
            "calendar_candidate_hash":calendar_hash,
            "next_end_bucket":expected_first,
            "complete_issuer_window_observations":0,
            "compressed_derived_bytes":0,
            "last_window_sha256":None,
            "vendor_raw_file_count":0,
        }
    try: obj=json.loads(raw)
    except Exception: raise DerivedReturnError("invalid_composed_return_checkpoint") from None
    end=obj.get("next_end_bucket")
    if (obj.get("schema")!=CHECKPOINT_SCHEMA
        or not _calendar_hash_matches(obj.get("calendar_candidate_hash"),
                                      calendar_hash,dates,5*end if type(end) is int else -1)
        or obj.get("horizon")!=horizon
        or type(end) is not int or not expected_first<=end<=1000
        or type(obj.get("complete_issuer_window_observations")) is not int
        or obj["complete_issuer_window_observations"]<0
        or type(obj.get("compressed_derived_bytes")) is not int
        or not 0<=obj["compressed_derived_bytes"]<=MAX_HISTORY_BYTES
        or obj.get("vendor_raw_file_count")!=0
        or (end==expected_first and obj.get("last_window_sha256") is not None)
        or (end>expected_first and (type(obj.get("last_window_sha256")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}",obj["last_window_sha256"])))):
        raise DerivedReturnError("invalid_composed_return_checkpoint")
    return obj


def run(*,storage,dates,calendar_hash,limit=MAX_PER_HORIZON,horizons=HORIZONS):
    if (storage.repository!="ykvic1/korean-stock-private-data"
        or type(dates) is not list or dates!=sorted(set(dates)) or len(dates)<10
        or type(calendar_hash) is not str or not re.fullmatch(r"[a-f0-9]{64}",calendar_hash)
        or type(limit) is not int or not 1<=limit<=MAX_PER_HORIZON
        or not isinstance(horizons,(tuple,list))
        or any(h not in HORIZONS for h in horizons)
        or len(set(horizons))!=len(horizons)):
        raise DerivedReturnError("invalid_rolling_factor_configuration")
    storage.verify_private()
    historic_data,_=storage.get_content(HISTORY_CHECKPOINT)
    if historic_data is None:
        raise DerivedReturnError("historical_derived_source_not_available")
    try:
        historical=_history_manifest(historic_data,dates,calendar_hash)
    except Exception:
        raise DerivedReturnError("invalid_historical_derived_checkpoint") from None
    complete=historical["next_window_index"]
    boundaries,_=storage.get_content(BRIDGE_CHECKPOINT)
    if boundaries is None:
        return {"status":"waiting_for_derived_cohort_bridges","provider_api_calls":0}
    try:
        bridged=_bridge_progress(boundaries,dates,calendar_hash,complete)
    except Exception:
        raise DerivedReturnError("invalid_derived_boundary_checkpoint") from None
    max_available=min(complete,bridged["next_boundary_index"])
    report={"schema":"v3-private-rolling-derived-observation-coverage-v1",
            "historical_complete_cohorts":complete,
            "completed_cross_bucket_links":bridged["next_boundary_index"]-1,
            "source_window_session_cap":max_available*5,
            "provider_api_calls":0,
            "raw_source_files_added":0,
            "confirmed_v3_trade_scoring":False,
            "horizons":{}}
    for horizon in horizons:
        b=horizon//5
        raw,sha=storage.get_content(_progress_path(horizon))
        progress=_check_progress(raw,calendar_hash,horizon)
        pointer=progress["next_end_bucket"]
        if pointer>b:
            prev_end=pointer-1
            prior_path=_file(dates[5*(prev_end-b)],dates[5*prev_end-1],horizon)
            previous,_=storage.get_content(prior_path)
            if previous is None or sha256(previous).hexdigest()!=progress["last_window_sha256"]:
                raise DerivedReturnError("last_private_rolling_file_missing_or_damaged")
            _validate_rolling(previous,horizon,dates[5*(prev_end-b)],dates[5*prev_end-1])
        until=min(max_available,pointer+limit-1)
        factor_cache={};link_cache={};windows=0;new_bytes=0;observations=0
        resumed=0;last_digest=progress["last_window_sha256"]
        for end in range(pointer,until+1):
            first,last=dates[5*(end-b)],dates[5*end-1]
            name=_file(first,last,horizon)
            existing,_=storage.get_content(name)
            if existing is not None:
                summary=_validate_rolling(existing,horizon,first,last)
                encoded=existing
                resumed+=1
            else:
                if progress["compressed_derived_bytes"]+new_bytes+MAX_BLOB>=MAX_HISTORY_BYTES:
                    raise DerivedReturnError("rolling_history_capacity_limit")
                summary,encoded=_estimate_one(storage,dates,horizon,end,
                                              factor_cache,link_cache)
                storage.put_new(name,encoded,
                                "Save private "+str(horizon)+"-session derived research "+last)
                _readback_with_retry(storage,name,encoded)
            windows+=1
            observations+=summary["complete_issuer_count"]
            new_bytes+=len(encoded)
            last_digest=sha256(encoded).hexdigest()
        if windows:
            updated={
                "schema":CHECKPOINT_SCHEMA,"horizon":horizon,
                "calendar_candidate_hash":calendar_hash,
                "next_end_bucket":pointer+windows,
                "complete_issuer_window_observations":
                    progress["complete_issuer_window_observations"]+observations,
                "compressed_derived_bytes":progress["compressed_derived_bytes"]+new_bytes,
                "last_window_sha256":last_digest,
                "vendor_raw_file_count":0,
            }
            raw=_json(updated)
            if len(raw)>1300:
                raise DerivedReturnError("composed_progress_checkpoint_oversized")
            if sha is None:
                storage.put_new(_progress_path(horizon),raw,
                                "Start private "+str(horizon)+"-session derived progress")
            else:
                storage.transport("PUT","contents/"+_progress_path(horizon),{
                    "message":"Advance private "+str(horizon)+"-session derived research",
                    "content":base64.b64encode(raw).decode("ascii"),
                    "sha":sha})
            _readback_with_retry(storage,_progress_path(horizon),raw)
            progress=updated
        report["horizons"][str(horizon)]={
            "saved_complete_cohort_windows":progress["next_end_bucket"]-b,
            "saved_complete_issuer_window_observations":
                progress["complete_issuer_window_observations"],
            "private_compressed_bytes":progress["compressed_derived_bytes"],
            "newly_written_or_reused_windows":windows,
            "preexisting_uncheckpointed_files_reused":resumed,
            "ready_to_calculate_from_derived":max_available>=b,
            "adjusted_price_return_verified":False,
        }
    return report


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()):
        print('{"status":"blocked","reason":"private_rolling_factor_secrets_missing"}')
        return 1
    try:
        days,digest=history_candidates()
        store=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        outcome=run(storage=store,dates=days,calendar_hash=digest)
        print(json.dumps(outcome,sort_keys=True))
        return 0
    except DerivedReturnError as e:
        print(json.dumps({"status":"blocked","reason":str(e),"raw_data_exposed":False}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"private_derived_factor_read_or_write_failed","raw_data_exposed":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
