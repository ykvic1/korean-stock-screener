"""Screen genuine PRIVATE KRX derived-history price discontinuity risks.

This source-independent QC NEVER proves any actual split, bonus issue, merger
or adjusted historical close. It only flags abnormally large four-interval
bucket returns or one-interval between-bucket returns for issuer-level review.

No KRX provider calls, no raw OHLCV downloads, no public Pages output and
NO V3 trade-score promotion. Immutable output is restricted to private Git.
"""
from __future__ import annotations

from hashlib import sha256
import gzip
import json
import math
import os
import re

from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_derived_backfill import (
    history_candidates, _manifest as validate_checkpoint, CHECKPOINT as HIST_PATH,
    _path as factor_path)
from v3_krx_derived_5day import validate_derived_blob
from v3_krx_derived_bridges import (
    _read_link, _link_path, CHECKPOINT as BRIDGE_PATH,
    _check_progress as validate_bridge_progress, _readback_with_retry)

SCHEMA="v3-private-krx-price-discontinuity-risk-audit-v1"
# Heuristic screening thresholds, NOT an exchange corporate-action certificate.
BUCKET_JUMP_BP=3000          # +/-30% between bucket first/last closes
BOUNDARY_ABS_LOG_E9=300_000_000  # abs(log(Cnext / Cprev)) >=0.30 (~35% / -26%)
MAX_COHORTS=500
MAX_FLAGS=200_000
MAX_COMPRESSED=1_000_000
CODE=re.compile(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}\Z")


class CorporateRiskError(ValueError):
    """Sanitized no-data error codes only."""


def _bytes(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),
                      ensure_ascii=True,allow_nan=False).encode("utf8")


def _file_end(dates,cohorts):
    return "derived/quality/price-discontinuities-"+dates[5*cohorts-1]+".json.gz"


def _read_existing(blob,lastday,cohorts):
    try:
        if type(blob) is not bytes or not 0<len(blob)<MAX_COMPRESSED:
            raise ValueError()
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
            decompressed=stream.read(15_000_001)
        if len(decompressed)>15_000_000:
            raise ValueError()
        data=json.loads(decompressed)
        if (data.get("schema")!=SCHEMA or data.get("last_completed_session")!=lastday
            or data.get("completed_five_session_cohorts")!=cohorts
            or data.get("source_provider_calls")!=0
            or data.get("original_ohlcv_stored") is not False
            or data.get("corporate_actions_verified") is not False
            or data.get("scores_or_buy_signals_approved") is not False
            or type(data.get("flagged_issuer_periods")) is not list
            or len(data["flagged_issuer_periods"])!=data.get("total_flags")
            or len(data["flagged_issuer_periods"])>MAX_FLAGS):
            raise ValueError()
        for f in data["flagged_issuer_periods"]:
            if (not isinstance(f,dict) or type(f.get("issuer_market")) is not str
                or not CODE.fullmatch(f["issuer_market"])
                or f.get("kind") not in ("large_four_interval_bucket_change",
                                         "large_one_interval_cohort_boundary")
                or type(f.get("quantized_change_bp")) is not int
                or type(f.get("period_start")) is not str
                or type(f.get("period_end")) is not str
                or f["period_start"]>=f["period_end"]):
                raise ValueError()
        if any(k in decompressed for k in
               (b'"TDD_CLSPRC"',b'"ACC_TRDVOL"',b'"raw_source_base64"')):
            raise ValueError()
        return data
    except Exception:
        raise CorporateRiskError("private_quality_evidence_invalid") from None


def collect(*,storage,dates,checksum):
    """Summarize ALL actually completed and bridged private 5day cohorts."""
    if (storage.repository!="ykvic1/korean-stock-private-data"
        or type(dates) is not list or dates!=sorted(set(dates))
        or type(checksum) is not str or not re.fullmatch(r"[0-9a-f]{64}",checksum)
        or not 5<=len(dates)<=6000):
        raise CorporateRiskError("invalid_private_quality_source_configuration")
    storage.verify_private()
    raw,_=storage.get_content(HIST_PATH)
    if raw is None:
        raise CorporateRiskError("historical_derived_checkpoint_absent")
    try:
        history=validate_checkpoint(raw,dates,checksum)
    except Exception:
        raise CorporateRiskError("historical_derived_checkpoint_untrusted") from None
    available=history["next_window_index"]
    if not 1<=available<=MAX_COHORTS:
        raise CorporateRiskError("no_completed_5session_derived_sources")
    bridges,_=storage.get_content(BRIDGE_PATH)
    if bridges is None:
        if available>1:
            raise CorporateRiskError("boundary_derived_checkpoint_absent")
        link_available=0
    else:
        try:
            progress=validate_bridge_progress(bridges,dates,checksum,available)
            link_available=progress["next_boundary_index"]-1
        except Exception:
            raise CorporateRiskError("untrusted_boundary_derived_checkpoint") from None
    # Never claim a complete price-chain quality review unless all boundaries
    # between the observed cohorts are actually present and verified.
    if link_available!=available-1:
        raise CorporateRiskError("unreviewed_cross_cohort_boundary_gaps")
    last=dates[5*available-1]
    path=_file_end(dates,available)
    old,_=storage.get_content(path)
    if old is not None:
        saved=_read_existing(old,last,available)
        return {
            "status":"reused_existing_private_quality_audit",
            "source_cohorts":available,
            "reviewed_cross_bucket_links":link_available,
            "risk_flags":saved["total_flags"],
            "flagged_distinct_issuers":saved["distinct_flagged_issuer_markets"],
            "source_provider_calls":0,
            "raw_prices_saved":0,
        }
    issues=[]
    observed_buckets=0
    observed_links=0
    unique=set()
    flagged_buckets=set()
    for n in range(available):
        days=dates[n*5:(n+1)*5]
        src,_=storage.get_content(factor_path(days))
        if src is None:
            raise CorporateRiskError("missing_private_derived_factor_cohort")
        try:
            factor=validate_derived_blob(src,days[0],days[-1])
        except Exception:
            raise CorporateRiskError("invalid_private_derived_factor_cohort") from None
        for issuer,row in factor["derived_issuer_factors"].items():
            if type(issuer) is not str or not CODE.fullmatch(issuer):
                raise CorporateRiskError("invalid_private_derived_stock_identity")
            ratio=row.get("intrabucket_four_interval_return_bp")
            if type(ratio) is not int:
                raise CorporateRiskError("invalid_private_derived_period_return")
            observed_buckets+=1
            if abs(ratio)>=BUCKET_JUMP_BP:
                issues.append({
                    "issuer_market":issuer,
                    "period_start":days[0],"period_end":days[-1],
                    "kind":"large_four_interval_bucket_change",
                    "quantized_change_bp":ratio,
                    "exchange_corporate_event_verified":False})
                unique.add(issuer)
                flagged_buckets.add(n)
        if n:
            before=dates[n*5-1]
            after=days[0]
            link,_=storage.get_content(_link_path(before,after))
            if link is None:
                raise CorporateRiskError("missing_private_derived_cross_boundary")
            try:
                bridge=_read_link(link,before,after)
            except Exception:
                raise CorporateRiskError("unverified_private_derived_cross_boundary") from None
            for issuer,row in bridge["derived_logreturn_by_market_issuer"].items():
                if type(issuer) is not str or not CODE.fullmatch(issuer):
                    raise CorporateRiskError("invalid_private_cross_boundary_identity")
                observed_links+=1
                log_e9=row["cross_bucket_log_return_e9"]
                if abs(log_e9)>=BOUNDARY_ABS_LOG_E9:
                    # Logreturn is converted ONLY to a rounded, derived bp.
                    # It is not an absolute KRX price or a proof of split.
                    bp=round(math.expm1(log_e9/1e9)*10000)
                    issues.append({
                        "issuer_market":issuer,
                        "period_start":before,"period_end":after,
                        "kind":"large_one_interval_cohort_boundary",
                        "quantized_change_bp":bp,
                        "exchange_corporate_event_verified":False})
                    unique.add(issuer)
                    flagged_buckets.add(n)
                    flagged_buckets.add(n-1)
        if len(issues)>MAX_FLAGS:
            raise CorporateRiskError("excessive_historical_quality_review_flags")
    # Conservative time-level count only. A flagged time bucket does not prove
    # that ALL other issuers in the bucket are problematic or action-adjusted.
    summary={
        "schema":SCHEMA,
        "historical_calendar_candidate_hash":checksum,
        "last_completed_session":last,
        "completed_five_session_cohorts":available,
        "reviewed_cross_bucket_links":link_available,
        "observed_issuer_bucket_factors":observed_buckets,
        "observed_issuer_boundary_links":observed_links,
        "bucket_abs_return_threshold_bp":BUCKET_JUMP_BP,
        "boundary_abs_logreturn_threshold_e9":BOUNDARY_ABS_LOG_E9,
        "total_flags":len(issues),
        "distinct_flagged_issuer_markets":len(unique),
        "cohorts_touching_one_or_more_price_flags":len(flagged_buckets),
        "flagged_issuer_periods":issues,
        "source_provider_calls":0,
        "original_ohlcv_stored":False,
        "corporate_actions_verified":False,
        "issuer_survivorship_or_pit_verified":False,
        "unflagged_stocks_action_free_certified":False,
        "adjusted_price_returns_certified":False,
        "scores_or_buy_signals_approved":False,
        "personal_private_research_only":True,
    }
    gz=gzip.compress(_bytes(summary),compresslevel=9,mtime=0)
    if not 0<len(gz)<MAX_COMPRESSED:
        raise CorporateRiskError("oversized_derived_risk_audit")
    storage.put_new(path,gz,"Store private derived KRX abnormal return screening audit "+last)
    _readback_with_retry(storage,path,gz)
    _read_existing(gz,last,available)
    return {
        "status":"private_derived_price_jump_screen_verified",
        "historical_calendar_last_observed":last,
        "source_cohorts":available,
        "reviewed_cross_bucket_links":link_available,
        "observed_issuer_bucket_factors":observed_buckets,
        "observed_issuer_boundary_links":observed_links,
        "risk_flags":len(issues),
        "flagged_distinct_issuers":len(unique),
        "private_compressed_bytes":len(gz),
        "source_provider_calls":0,
        "raw_prices_saved":0,
        "corporate_actions_confirmed":False,
        "v3_score_certified":False,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO")!="ykvic1/korean-stock-private-data"
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","").strip()):
        print('{"status":"blocked","reason":"private_quality_archive_not_connected"}')
        return 1
    try:
        days,digest=history_candidates()
        storage=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                  os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        report=collect(storage=storage,dates=days,checksum=digest)
        print(json.dumps(report,sort_keys=True))
        return 0
    except CorporateRiskError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),
                          "source_values_exposed":False},sort_keys=True))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"invalid_private_derived_source_or_transport","source_values_exposed":false}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
