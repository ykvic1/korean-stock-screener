"""Finish trailing FOUR XKRX date candidates using RAM-only official OHLCV.

The historical backfill groups first 2,450 dates in 490 complete 5-session
cohorts; four leftover candidates are NOT interpolated or treated as a 5-day
bucket. Capture a separate 4-session, 3-interval derived-only summary, with
zero persisted OHLCV, only in the private archive. Never roll it into 20d.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import date
import gzip
import json
import math
import os
import re

from v3_krx_derived_backfill import history_candidates
from v3_krx_derived_5day import _row_values,DerivedSourceError
from v3_krx_history_collection import _snapshot_market
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA="v3-krx-private-four-session-tail-research-v1"
MAX_FILE_BYTES=300000
MAX_HISTORIC_STOCKS=6500


class PartialTailError(ValueError):
    """Only sanitized static codes; no supplier error texts."""


def select_tail(days):
    if (type(days) is not list or len(days)<4
        or len(days)!=len(set(days)) or days!=sorted(days)
        or any(type(d) is not str for d in days)):
        raise PartialTailError("invalid_historical_session_candidates")
    remainder=len(days)%5
    if remainder!=4:
        return None
    selected=days[-4:]
    for d in selected:
        try:
            if date.fromisoformat(d).isoformat()!=d:
                raise ValueError()
        except ValueError:
            raise PartialTailError("invalid_four_session_candidate_day") from None
    return selected


def _canonical(obj):
    return json.dumps(obj,ensure_ascii=True,allow_nan=False,
                      separators=(",",":"),sort_keys=True).encode()


def _readback(storage,path,blob):
    # See GitHub's briefly invisible Contents after PUT: no duplicate KRX
    # source requests or duplicate files, only bounded same-file GET retries.
    import time
    for i in range(4):
        got,_=storage.get_content(path)
        if got==blob:
            return True
        if i<3:time.sleep(.7*(i+1))
    raise PartialTailError("partial_private_write_readback_not_visible")


def derive(client,days):
    if (type(days) not in (tuple,list) or len(days)!=4
        or list(days)!=sorted(set(days))
        or any(type(x) is not str for x in days)):
        raise PartialTailError("invalid_four_session_window")
    by_market={"KOSPI":{},"KOSDAQ":{}}
    source_sizes=0
    quality={}
    calls=0
    try:
        for d in days:
            for market in ("KOSPI","KOSDAQ"):
                rows,raw=client.daily_market(market,d)
                stats=_snapshot_market(rows,raw,market=market,asof=d)
                quality[d+"/"+market]={
                    "provider_rows":stats["provider_rows_observed"],
                    "usable_numeric_rows":stats["observed_numeric_candidates"],
                    "quarantined_rows":stats["quarantined_unpriced_candidates"]}
                calls+=1
                source_sizes+=len(raw)
                for row in rows:
                    code=row.get("ISU_CD")
                    if type(code) is not str or not re.fullmatch(r"[0-9A-Z]{6}",code):
                        raise ValueError()
                    v=_row_values(row)
                    if v is not None:
                        by_market[market].setdefault(code,{})[d]=v
                del raw,rows
    except Exception:
        raise PartialTailError("four_session_official_source_or_integrity_failure") from None
    if calls!=8 or len(quality)!=8:
        raise PartialTailError("incomplete_four_session_official_market_pairs")
    values={}
    for market in ("KOSPI","KOSDAQ"):
        for code,history in by_market[market].items():
            if len(history)!=4:
                continue
            points=[history[d] for d in days]
            close=[p[0] for p in points]
            vol=[p[1] for p in points]
            ratio=close[-1]/close[0]-1
            moves=[close[i]/close[i-1]-1 for i in range(1,4)]
            average=sum(moves)/3
            voltotal=sum(vol[1:])
            delta=sum((1 if close[i]>close[i-1] else -1 if close[i]<close[i-1]
                       else 0)*vol[i] for i in range(1,4))
            if not math.isfinite(ratio) or abs(ratio)>100:
                continue
            values[market+":"+code]={
                "observed_sessions":4,
                "observed_return_intervals":3,
                "intrabucket_three_interval_return_bp":round(ratio*10000),
                "intrabucket_daily_return_volatility_bp":round(
                    math.sqrt(sum((r-average)**2 for r in moves)/3)*10000),
                "normalized_obv_change_bp":round(delta/voltotal*10000)
                                           if voltotal>0 else None,
                "security_adjustment_certified":False,
                "eligible_for_five_session_chain":False,
                "absolute_price_or_volume_retained":False,
            }
    if len(values)>MAX_HISTORIC_STOCKS:
        raise PartialTailError("excessive_historical_tail_stock_count")
    summary={
        "schema":SCHEMA,"window_start":days[0],"window_end":days[-1],
        "source_api_calls":calls,"observed_market_date_pairs":len(quality),
        "market_source_quality_counts":quality,
        "observed_four_session_issuers":len(values),
        "per_issuer_partial_factors":values,
        "source_vendor_bytes_transient":source_sizes,
        "original_market_json_retained":False,
        "absolute_ohlcv_retained":False,
        "valid_complete_five_day_cohort":False,
        "join_20_60_120_250_allowed":False,
        "corporate_action_and_pit_verified":False,
        "v3_six_pillar_score_certified":False,
    }
    blob=gzip.compress(_canonical(summary),compresslevel=9,mtime=0)
    if not 0<len(blob)<MAX_FILE_BYTES:
        raise PartialTailError("four_session_private_derived_too_large")
    return summary,blob


def validate(blob,expected):
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_FILE_BYTES:
            raise ValueError()
        from io import BytesIO
        with gzip.GzipFile(fileobj=BytesIO(blob)) as z:
            raw=z.read(6_000_001)
        if len(raw)>6_000_000:
            raise ValueError()
        item=json.loads(raw)
        if (item.get("schema")!=SCHEMA
            or item.get("window_start")!=expected[0]
            or item.get("window_end")!=expected[-1]
            or item.get("source_api_calls")!=8
            or item.get("observed_market_date_pairs")!=8
            or item.get("absolute_ohlcv_retained") is not False
            or item.get("valid_complete_five_day_cohort") is not False
            or item.get("join_20_60_120_250_allowed") is not False
            or type(item.get("per_issuer_partial_factors")) is not dict
            or len(item["per_issuer_partial_factors"])!=item["observed_four_session_issuers"]):
            raise ValueError()
        for k,v in item["per_issuer_partial_factors"].items():
            if (type(k) is not str
                or not re.fullmatch(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}",k)
                or v.get("observed_sessions")!=4
                or type(v.get("intrabucket_three_interval_return_bp")) is not int
                or v.get("eligible_for_five_session_chain") is not False):
                raise ValueError()
        if any(k in raw for k in (b'"TDD_CLSPRC"',b'"ACC_TRDVOL"',b'"raw_source_base64"')):
            raise ValueError()
        return item
    except Exception:
        raise PartialTailError("private_four_session_derived_integrity_failure") from None


def store(storage,client,days):
    if storage.repository!="ykvic1/korean-stock-private-data":
        raise PartialTailError("private_derived_destination_required")
    tail=select_tail(days)
    if not tail:
        return {"status":"no_four_session_remainder","provider_api_calls":0,
                "original_source_files_saved":0}
    storage.verify_private()
    path="derived/partial-tail/"+tail[0]+"_"+tail[-1]+".json.gz"
    old,_=storage.get_content(path)
    if old is not None:
        info=validate(old,tail)
        return {"status":"reused_verified_four_session_tail","provider_api_calls":0,
                "observed_four_session_issuers":info["observed_four_session_issuers"],
                "private_derived_file_bytes":len(old),"original_source_files_saved":0}
    info,blob=derive(client,tail)
    storage.put_new(path,blob,"Store KRX 4-session nonreconstructive partial tail "+tail[-1])
    _readback(storage,path,blob)
    verified=validate(blob,tail)
    return {"status":"verified_saved_four_session_tail",
            "dates":tail,"provider_api_calls":8,
            "observed_four_session_issuers":verified["observed_four_session_issuers"],
            "private_derived_file_bytes":len(blob),
            "transient_provider_bytes":info["source_vendor_bytes_transient"],
            "original_source_files_saved":0,
            "converts_to_verified_20day":False}


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()
        or not os.getenv("KRX_API_KEY","").strip()):
        print('{"status":"blocked","reason":"derived_private_or_provider_secrets_missing"}')
        return 1
    try:
        from official_probe.clients import KRXClient
        days,_=history_candidates()
        archive=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                  os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        result=store(archive,KRXClient(os.environ["KRX_API_KEY"]),days)
        print(json.dumps(result,sort_keys=True))
        return 0
    except PartialTailError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),"raw_logged":False}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"source_or_private_derived_unavailable","raw_logged":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
