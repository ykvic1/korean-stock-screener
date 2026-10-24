"""Private KRX session-direction evidence from saved five-session and boundary factors.

This is a research-only observation of rising close-to-close *intervals*,
not 20/60/120 official day gains, adjusted stock prices, a momentum score,
or a reliable point-in-time tradable signal. Nothing raw or issuer-specific
is published to the public repository. Zero new KRX/DART provider calls.
"""
from __future__ import annotations

import gzip
from hashlib import sha256
from io import BytesIO
import json
import os
import re

from v3_krx_derived_backfill import (
    CHECKPOINT as HISTORY_CHECKPOINT, _manifest as _history_manifest,
    history_candidates)
from v3_krx_derived_bridges import (
    CHECKPOINT as BRIDGE_CHECKPOINT, _check_progress as _bridge_progress,
    _readback_with_retry)
from v3_krx_derived_rolling_returns import _read_factor, _read_bridge
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA="v3-krx-private-quantized-direction-counts-research-v1"
HORIZONS=(20,60,120)
MAX_BLOB=350_000
MAX_ISSUERS=6_000


class DirectionEvidenceError(ValueError):
    """Fixed status names; never log raw vendor data or private file paths."""


def _canonical(x):
    return json.dumps(x,sort_keys=True,ensure_ascii=True,
                      separators=(",",":"),allow_nan=False).encode("ascii")


def _path(start,end,horizon):
    return f"derived/momentum-direction/{horizon}/{start}_{end}.json.gz"


def derive_window(storage,dates,horizon,end_bucket):
    """Exactly H sessions contain H-1 within/between-bucket intervals."""
    if horizon not in HORIZONS or end_bucket<horizon//5:
        raise DirectionEvidenceError("invalid_direction_window")
    b=horizon//5
    start_bucket=end_bucket-b
    if 5*end_bucket>len(dates):
        raise DirectionEvidenceError("direction_dates_incomplete")
    factor_cache={}
    bridge_cache={}
    factors=[_read_factor(storage,dates,n) for n in range(start_bucket,end_bucket)]
    bridges=[_read_bridge(storage,dates,n) for n in range(start_bucket+1,end_bucket)]
    if len(factors)!=b or len(bridges)!=b-1:
        raise DirectionEvidenceError("direction_intervals_incomplete")
    keys=set(factors[0])
    for block in factors[1:]:
        keys.intersection_update(block)
    for link in bridges:
        keys.intersection_update(link)
    if len(keys)>MAX_ISSUERS:
        raise DirectionEvidenceError("direction_issuer_limit")
    evidence={}
    for key in sorted(keys):
        if not re.fullmatch(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}",key):
            raise DirectionEvidenceError("invalid_direction_issuer_key")
        block_signs=[]
        boundary_signs=[]
        for block in factors:
            row=block[key]
            n=row.get("rising_close_intervals")
            if (type(n) is not int or not 0<=n<=4 or
                row.get("observed_sessions")!=5 or
                row.get("security_adjustment_certified") is not False or
                row.get("release_score_certified") is not False or
                row.get("daily_source_price_is_undisclosed") is not True):
                raise DirectionEvidenceError("untrusted_private_direction_factor")
            block_signs.append(n)
        for bridge in bridges:
            row=bridge[key]
            log= row.get("cross_bucket_log_return_e9")
            if (type(log) is not int or abs(log)>10_000_000_000 or
                row.get("issuer_identity_across_corporate_actions_verified") is not False or
                row.get("corporate_action_adjusted") is not False or
                row.get("source_absolute_closes_stored") is not False):
                raise DirectionEvidenceError("untrusted_private_direction_link")
            boundary_signs.append(int(log>0))
        rising=sum(block_signs)+sum(boundary_signs)
        total=horizon-1
        if not 0<=rising<=total:
            raise DirectionEvidenceError("direction_interval_count_out_of_range")
        item={"rising_close_intervals":rising,
              "flat_or_down_quantized_intervals":total-rising}
        if horizon==120:
            # Separate equal 60-session halves. Their central boundary is
            # excluded from BOTH halves, but included in the 119 total.
            first=sum(block_signs[:12])+sum(boundary_signs[:11])
            last=sum(block_signs[12:])+sum(boundary_signs[12:])
            if first+last+boundary_signs[11]!=rising or first>59 or last>59:
                raise DirectionEvidenceError("direction_breadth_partition_invalid")
            item["early_60session_rising_intervals"]=first
            item["late_60session_rising_intervals"]=last
        evidence[key]=item
    report={
        "schema":SCHEMA,
        "window_start":dates[5*start_bucket],
        "window_end":dates[5*end_bucket-1],
        "horizon_observed_sessions":horizon,
        "total_close_to_close_intervals":horizon-1,
        "complete_issuer_count":len(evidence),
        "issuer_market_private_direction_counts":evidence,
        "source_five_session_cohorts":b,
        "source_cross_cohort_links":b-1,
        "vendor_api_calls":0,
        "vendor_raw_source_retained":False,
        "source_price_or_volume_retained":False,
        "historical_listed_universe_pit_verified":False,
        "corporate_actions_adjusted":False,
        "benchmark_session_alignment_independently_verified":False,
        "quantized_direction_near_zero_uncertainty":True,
        "live_momentum_extra_seven_points_approved":False,
        "individual_investment_score_certified":False,
        "for_private_noncommercial_research_only":True,
    }
    packed=gzip.compress(_canonical(report),compresslevel=9,mtime=0)
    if not 0<len(packed)<MAX_BLOB:
        raise DirectionEvidenceError("private_direction_blob_size_invalid")
    return report,packed


def validate_saved(blob,horizon,start,end):
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_BLOB:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as reader:
            data=reader.read(8_000_001)
        if len(data)>8_000_000:
            raise ValueError()
        o=json.loads(data)
        if (o.get("schema")!=SCHEMA or
            o.get("horizon_observed_sessions")!=horizon or
            o.get("window_start")!=start or o.get("window_end")!=end or
            o.get("total_close_to_close_intervals")!=horizon-1 or
            o.get("source_five_session_cohorts")!=horizon//5 or
            o.get("source_cross_cohort_links")!=horizon//5-1 or
            o.get("vendor_api_calls")!=0 or
            o.get("vendor_raw_source_retained") is not False or
            o.get("source_price_or_volume_retained") is not False or
            o.get("historical_listed_universe_pit_verified") is not False or
            o.get("corporate_actions_adjusted") is not False or
            o.get("benchmark_session_alignment_independently_verified") is not False or
            o.get("quantized_direction_near_zero_uncertainty") is not True or
            o.get("live_momentum_extra_seven_points_approved") is not False or
            o.get("individual_investment_score_certified") is not False):
            raise ValueError()
        pairs=o["issuer_market_private_direction_counts"]
        if (type(pairs) is not dict or
            len(pairs)!=o["complete_issuer_count"] or len(pairs)>MAX_ISSUERS):
            raise ValueError()
        for key,row in pairs.items():
            if (not re.fullmatch(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}",key)
                or type(row) is not dict
                or type(row.get("rising_close_intervals")) is not int
                or type(row.get("flat_or_down_quantized_intervals")) is not int
                or row["rising_close_intervals"]<0
                or row["flat_or_down_quantized_intervals"]<0
                or row["rising_close_intervals"]+
                   row["flat_or_down_quantized_intervals"]!=horizon-1):
                raise ValueError()
            if horizon==120:
                first=row.get("early_60session_rising_intervals")
                last=row.get("late_60session_rising_intervals")
                if (type(first) is not int or type(last) is not int
                    or not 0<=first<=59 or not 0<=last<=59
                    or first+last>row["rising_close_intervals"]
                    or row["rising_close_intervals"]-first-last>1):
                    raise ValueError()
            elif set(row)!={"rising_close_intervals","flat_or_down_quantized_intervals"}:
                raise ValueError()
        if any(k in data for k in (b'"TDD_CLSPRC"',b'"ACC_TRDVOL"',b'"raw_source_base64"')):
            raise ValueError()
        return o
    except Exception:
        raise DirectionEvidenceError("private_direction_archive_invalid") from None


def run(*,storage,dates,calendar_hash,horizons=HORIZONS):
    if (storage.repository!="ykvic1/korean-stock-private-data"
        or type(dates) is not list or dates!=sorted(set(dates))
        or len(dates)<120 or type(calendar_hash) is not str
        or not re.fullmatch(r"[a-f0-9]{64}",calendar_hash)
        or type(horizons) not in (tuple,list) or len(set(horizons))!=len(horizons)
        or any(h not in HORIZONS for h in horizons)):
        raise DirectionEvidenceError("invalid_private_direction_configuration")
    storage.verify_private()
    historic_raw,_=storage.get_content(HISTORY_CHECKPOINT)
    if historic_raw is None:
        raise DirectionEvidenceError("missing_private_direction_history_checkpoint")
    try:
        historic=_history_manifest(historic_raw,dates,calendar_hash)
    except Exception:
        raise DirectionEvidenceError("untrusted_direction_history_checkpoint") from None
    raw_bridge,_=storage.get_content(BRIDGE_CHECKPOINT)
    if raw_bridge is None:
        return {"status":"waiting_for_verified_private_bridges",
                "provider_api_calls":0,"approved_extra_momentum_points":0}
    try:
        bridge=_bridge_progress(raw_bridge,dates,calendar_hash,
                                historic["next_window_index"])
    except Exception:
        raise DirectionEvidenceError("untrusted_direction_bridge_checkpoint") from None
    end_bucket=min(historic["next_window_index"],bridge["next_boundary_index"])
    result={"schema":"v3-private-verified-source-direction-coverage-v1",
            "latest_complete_source_cohorts":end_bucket,
            "provider_api_calls":0,
            "public_issuer_values_written":False,
            "approved_extra_momentum_points":0,
            "historical_adjustment_pit_verified":False,
            "horizons":{}}
    for horizon in horizons:
        b=horizon//5
        if end_bucket<b:
            result["horizons"][str(horizon)]={"status":"waiting_for_complete_window"}
            continue
        first=dates[5*(end_bucket-b)]
        last=dates[5*end_bucket-1]
        path=_path(first,last,horizon)
        existing,_=storage.get_content(path)
        if existing is not None:
            report=validate_saved(existing,horizon,first,last)
            saved=False
        else:
            report,packed=derive_window(storage,dates,horizon,end_bucket)
            storage.put_new(path,packed,
                            "Save private quantized rising-session research "+last)
            _readback_with_retry(storage,path,packed)
            saved=True
        result["horizons"][str(horizon)]={
            "window_end":last,"issuer_observations":report["complete_issuer_count"],
            "private_immutable_created":saved,
            "adjusted_prices_verified":False,
            "official_momentum_score_promoted":False,
        }
    return result


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()):
        print('{"status":"blocked","reason":"private_direction_credentials_missing"}')
        return 1
    try:
        dates,digest=history_candidates()
        store=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        print(json.dumps(run(storage=store,dates=dates,calendar_hash=digest),
                         sort_keys=True))
        return 0
    except DirectionEvidenceError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),
                          "source_exposed":False}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"private_direction_source_unavailable","source_exposed":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
