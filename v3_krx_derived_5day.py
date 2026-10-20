"""Real KRX daily market -> privacy-safe, non-reconstructive 5-session factors.

The provider's complete raw JSON is TEMPORARY process memory only. Never
encode, archive, print, upload or checkpoint source bytes, OHLC or volume.
Only issuer-linked derived research observations and market coverage counts may
be saved in the user's PRIVATE GitHub repository. Derived status does not
waive upstream data licensing obligations.
"""
from __future__ import annotations

from datetime import date
import gzip
import json
import math
import os
import re
import time
from hashlib import sha256

from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError
from v3_krx_history_collection import _snapshot_market
from v3_krx_daily_anomaly_triage import classify_row
from official_probe.validation import _numeric

SCHEMA="v3-krx-private-derived-five-session-factors-0.1"
FIXED_FIRST_SAMPLE=["2026-10-12","2026-10-13","2026-10-14","2026-10-15","2026-10-16"]
MAX_SOURCE_CALLS=10
MAX_DATA_BYTES=500_000


class DerivedSourceError(ValueError):
    """Sanitized fixed error type; never propagate provider source or secret."""


def _check_days(days):
    if (type(days) not in (tuple,list) or len(days)!=5
        or any(type(d) is not str for d in days)
        or sorted(set(days))!=list(days)):
        raise DerivedSourceError("invalid_five_session_window")
    for d in days:
        try:
            parsed=date.fromisoformat(d)
        except ValueError:
            raise DerivedSourceError("invalid_five_session_date") from None
        if parsed.isoformat()!=d or parsed.weekday()>4:
            raise DerivedSourceError("invalid_five_session_date")
    return list(days)


def _bp(value):
    if not math.isfinite(value) or abs(value)>100:
        return None
    return int(round(value*10000))


def _row_values(row):
    try:
        if classify_row(row)[0]!="valid_numeric_observation":
            return None
        close=float(_numeric(row["TDD_CLSPRC"],"TDD_CLSPRC"))
        volume=float(_numeric(row["ACC_TRDVOL"],"ACC_TRDVOL"))
        if not (math.isfinite(close) and close>0 and math.isfinite(volume)
                and 0<=volume<1e15):
            return None
        return close,volume
    except Exception:
        return None


def derive(client,days=FIXED_FIRST_SAMPLE):
    """Read exactly 5 complete two-market provider sessions; return factors only.

    Cohort return is close(last) / close(first) - 1, i.e. FOUR intra-cohort
    intervals. It is not a calendar 5d daily-return chain with boundary return,
    and MUST NOT be used as a full 20/60/120/250-session proxy.
    """
    days=_check_days(days)
    frames={"KOSPI":{},"KOSDAQ":{}}
    counts={}
    raw_total=0
    fetched_calls=0
    try:
        for day in days:
            for market in ("KOSPI","KOSDAQ"):
                rows,raw=client.daily_market(market,day)
                source_check=_snapshot_market(rows,raw,market=market,asof=day)
                raw_total+=len(raw)
                fetched_calls+=1
                counts[f"{day}/{market}"]={
                    "provider_rows":source_check["provider_rows_observed"],
                    "observed_usable":source_check["observed_numeric_candidates"],
                    "unpriced_quarantined":source_check["quarantined_unpriced_candidates"],
                    "identity_not_independently_reconciled":True,
                }
                by_code=frames[market]
                for row in rows:
                    code=row.get("ISU_CD")
                    if type(code) is not str or not re.fullmatch(r"[0-9A-Z]{6}",code):
                        raise DerivedSourceError("bad_observed_stock_identity")
                    values=_row_values(row)
                    if values is not None:
                        by_code.setdefault(code,{})[day]=values
                # raw and rows are transient; only window-level numbers survive.
                del raw, rows
    except Exception:
        raise DerivedSourceError("official_market_window_or_validation_failed") from None
    if fetched_calls!=MAX_SOURCE_CALLS or len(counts)!=10:
        raise DerivedSourceError("incomplete_official_market_window")
    factors={}
    complete=0
    for market,observed in frames.items():
        for code,series in sorted(observed.items()):
            if len(series)!=5:
                # Report coverage in totals, but no misleading full-5d factor.
                continue
            daily=[series[d] for d in days]
            closes=[x[0] for x in daily]
            volumes=[x[1] for x in daily]
            returns=[closes[i]/closes[i-1]-1 for i in range(1,5)]
            r=_bp(closes[-1]/closes[0]-1)
            # Standard deviation of the four observed *within-window* returns.
            mean=sum(returns)/len(returns)
            rv=_bp(math.sqrt(sum((v-mean)**2 for v in returns)/len(returns)))
            first_vol=sum(volumes[:2]);last_vol=sum(volumes[-2:])
            volume_ratio=_bp(last_vol/first_vol-1) if first_vol>0 else None
            total_vol=sum(volumes[1:])
            signed=sum((1 if closes[i]>closes[i-1] else -1 if closes[i]<closes[i-1] else 0)
                       *volumes[i] for i in range(1,5))
            signed_flow=_bp(signed/total_vol) if total_vol>0 else None
            if r is None or rv is None:
                continue
            # A low-dimensional summary cannot reconstruct the 5 raw closes.
            factors[f"{market}:{code}"]={
                "market":market,
                "issuer_code":code,
                "observed_sessions":5,
                "intrabucket_four_interval_return_bp":r,
                "intrabucket_daily_return_volatility_bp":rv,
                "volume_last2_vs_first2_ratio_bp":volume_ratio,
                "normalized_obv_change_bp":signed_flow,
                "rising_close_intervals":sum(v>0 for v in returns),
                "daily_source_price_is_undisclosed":True,
                "raw_volume_is_undisclosed":True,
                "security_adjustment_certified":False,
                "release_score_certified":False,
            }
            complete+=1
    result={
        "schema":SCHEMA,
        "window_start":days[0],"window_end":days[-1],
        "source":"KRX_OPEN_API_KOSPI_KOSDAQ",
        "source_api_calls":fetched_calls,
        "market_date_coverage":counts,
        "complete_five_session_issuer_market_pairs":complete,
        "derived_issuer_factors":factors,
        "intrabucket_return_missing_four_intervals_across_boundaries":True,
        "not_full_20_60_120_250_session_history":True,
        "independent_listing_and_corporate_action_correction":False,
        "provider_raw_source_retained":False,
        "source_ohlcv_persisted":False,
        "publicly_published":False,
        "v3_six_pillar_full_scoring_allowed":False,
    }
    try:
        blob=gzip.compress(json.dumps(result,ensure_ascii=True,sort_keys=True,
                                      separators=(",",":"),allow_nan=False).encode("utf8"),
                           compresslevel=9,mtime=0)
        if not 0<len(blob)<MAX_DATA_BYTES:
            raise ValueError()
    except Exception:
        raise DerivedSourceError("derived_factor_payload_size_invalid") from None
    return result,blob,raw_total


def validate_derived_blob(blob,start,end):
    """Existing archived derived data can be revalidated without raw KRX calls."""
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_DATA_BYTES:
            raise ValueError()
        decoded=gzip.decompress(blob)
        if len(decoded)>4_000_000:
            raise ValueError()
        obj=json.loads(decoded)
        if (obj.get("schema")!=SCHEMA or obj.get("window_start")!=start
            or obj.get("window_end")!=end
            or obj.get("provider_raw_source_retained") is not False
            or obj.get("source_ohlcv_persisted") is not False
            or obj.get("source_api_calls")!=10
            or len(obj.get("market_date_coverage",{}))!=10
            or type(obj.get("derived_issuer_factors")) is not dict
            or obj.get("v3_six_pillar_full_scoring_allowed") is not False):
            raise ValueError()
        for key,x in obj["derived_issuer_factors"].items():
            if (key!=x.get("market")+":"+x.get("issuer_code")
                or x.get("observed_sessions")!=5
                or type(x.get("intrabucket_four_interval_return_bp")) is not int
                or any(k in x for k in ("TDD_CLSPRC","ACC_TRDVOL","raw_source_base64"))):
                raise ValueError()
        return obj
    except Exception:
        raise DerivedSourceError("existing_derived_archive_invalid") from None


def _private_new_blob_readback(storage,path,expected):
    """Verify a successful immutable GitHub Contents PUT despite short read lag.

    GET may temporarily report missing/stale bytes immediately after PUT.
    Retry bounded READ operations only: NEVER issue another file PUT, never
    re-call KRX for this observation and never treat mismatched bytes as valid.
    A later scheduled run reuses the already written file by its verified hash.
    """
    if type(expected) is not bytes or not expected:
        raise DerivedSourceError("private_derived_expected_blob_invalid")
    for attempt in range(4):
        try:
            received,_=storage.get_content(path)
        except PrivateHistoryError:
            received=None  # temporary GitHub HTTP trouble, no provider request
        if received==expected:
            return received
        if attempt<3:
            time.sleep(.7*(attempt+1))
    raise DerivedSourceError("private_derived_archive_readback_failed")


def store_five_day_factors(storage,client,days=FIXED_FIRST_SAMPLE,progress=None):
    days=_check_days(days)
    # Explicit location controls so an accidental token cannot publish results.
    if storage.repository!="ykvic1/korean-stock-private-data":
        raise DerivedSourceError("unexpected_destination_for_derived_research")
    if progress:progress("checking_private_repo_metadata")
    storage.verify_private()
    path=f"derived/five-session/{days[0]}_{days[-1]}.json.gz"
    if progress:progress("checking_private_derived_file")
    existing,_=storage.get_content(path)
    if existing is not None:
        saved=validate_derived_blob(existing,days[0],days[-1])
        return {"status":"verified_existing_derived","source_api_calls":0,
                "derived_file_bytes":len(existing),
                "complete_issuer_pairs":saved["complete_five_session_issuer_market_pairs"],
                "existing_file_sha256":sha256(existing).hexdigest()[:16],
                "raw_source_retained":False}
    if progress:progress("collecting_and_deriving_in_memory")
    obj,blob,raw_bytes=derive(client,days)
    # All calls and validation completed before the only private Git file write.
    if progress:progress("writing_private_derived_factor_file")
    storage.put_new(path,blob,"Archive five-session KRX derived research "+days[-1])
    if progress:progress("verifying_private_factor_write")
    actual=_private_new_blob_readback(storage,path,blob)
    saved=validate_derived_blob(actual,days[0],days[-1])
    return {"status":"persisted_and_readback_verified_derived",
            "source_api_calls":10,
            "derived_file_bytes":len(blob),
            "raw_response_transient_bytes":raw_bytes,
            "derived_vs_transient_source_size_saving_pct":round(100*(1-len(blob)/raw_bytes),2),
            "complete_issuer_pairs":saved["complete_five_session_issuer_market_pairs"],
            "market_day_checks":len(saved["market_date_coverage"]),
            "raw_source_retained":False,
            "price_ohlcv_publicly_distributed":False}


def main():
    if (not os.getenv("KRX_API_KEY","").strip()
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()
        or os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"):
        print('{"status":"blocked","reason":"api_key_or_private_archive_missing","krx_calls":0}')
        return 1
    try:
        from official_probe.clients import KRXClient
        store=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        result=store_five_day_factors(store,KRXClient(os.environ["KRX_API_KEY"]),
                                      progress=lambda stage:print("safe_stage="+stage,flush=True))
        print(json.dumps(result,sort_keys=True))
        return 0
    except (DerivedSourceError,PrivateHistoryError) as exc:
        # Both exception classes only carry fixed, non-vendor error strings.
        print(json.dumps({"status":"blocked",
                          "category":str(exc),
                          "source_values_exposed":False},sort_keys=True))
        return 1
    except Exception as exc:
        # Exception class alone is safe; NEVER log third-party error messages.
        print(json.dumps({"status":"blocked","category":"unknown_"+type(exc).__name__,
                          "source_values_exposed":False},sort_keys=True))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
