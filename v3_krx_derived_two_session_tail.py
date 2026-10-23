"""Persist the final TWO observed KRX sessions as PRIVATE derived-only research.

The verified historical calendar has 2,452 candidates: 490 complete five-day
cohorts and two trailing sessions. These two sessions yield ONE interval, not a
five-day cohort, a volatility estimate, or a certified investment score.
Provider OHLCV is held in memory only; the archive contains derived ratios.
"""
from __future__ import annotations

from datetime import date
import gzip
from io import BytesIO
import json
import math
import os
import re
import time

from v3_krx_derived_backfill import (
    CHECKPOINT, _manifest, _verify_last, history_candidates,
)
from v3_krx_derived_5day import _row_values
from v3_krx_history_collection import _snapshot_market
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA = "v3-krx-private-two-session-tail-research-v1"
MAX_FILE_BYTES = 350_000
MAX_ISSUERS = 6_500
PRIVATE_REPO = "ykvic1/korean-stock-private-data"


class TwoSessionTailError(ValueError):
    """All errors are static redacted categories; no provider response text."""


def select_tail(days):
    if (type(days) is not list or len(days) < 7 or
        len(days) != len(set(days)) or days != sorted(days) or
        any(type(x) is not str for x in days)):
        raise TwoSessionTailError("invalid_session_candidates")
    if len(days) % 5 != 2:
        return None
    tail = days[-2:]
    for day in tail:
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except ValueError:
            raise TwoSessionTailError("invalid_two_session_day") from None
    return tail


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def derive(client, days):
    if (type(days) not in (list, tuple) or len(days) != 2 or
        list(days) != sorted(set(days)) or
        any(type(x) is not str for x in days)):
        raise TwoSessionTailError("invalid_two_session_window")
    for day in days:
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except ValueError:
            raise TwoSessionTailError("invalid_two_session_day") from None

    histories = {"KOSPI": {}, "KOSDAQ": {}}
    quality = {}
    transient_bytes = 0
    try:
        for day in days:
            for market in ("KOSPI", "KOSDAQ"):
                rows, raw = client.daily_market(market, day)
                stats = _snapshot_market(rows, raw, market=market, asof=day)
                quality[day + "/" + market] = {
                    "provider_rows": stats["provider_rows_observed"],
                    "usable_numeric_rows": stats["observed_numeric_candidates"],
                    "quarantined_rows": stats["quarantined_unpriced_candidates"],
                }
                transient_bytes += len(raw)
                for row in rows:
                    code = row.get("ISU_CD")
                    if type(code) is not str or not re.fullmatch(r"[0-9A-Z]{6}", code):
                        raise ValueError()
                    values = _row_values(row)
                    if values is not None:
                        histories[market].setdefault(code, {})[day] = values
                del rows, raw
    except Exception:
        raise TwoSessionTailError("two_session_official_source_or_integrity_failure") from None

    if len(quality) != 4:
        raise TwoSessionTailError("incomplete_two_session_market_pairs")

    factors = {}
    for market in ("KOSPI", "KOSDAQ"):
        for code, prices in histories[market].items():
            if len(prices) != 2:
                continue
            first = prices[days[0]][0]
            last = prices[days[1]][0]
            ratio = last / first - 1
            if not math.isfinite(ratio) or abs(ratio) > 100:
                continue
            factors[market + ":" + code] = {
                "observed_sessions": 2,
                "observed_return_intervals": 1,
                "intrabucket_one_interval_return_bp": round(ratio * 10000),
                "volatility_not_estimable_from_one_interval": True,
                "obv_trend_not_estimable_from_one_interval": True,
                "security_adjustment_certified": False,
                "eligible_for_five_session_chain": False,
                "absolute_price_or_volume_retained": False,
            }
    if not 0 < len(factors) <= MAX_ISSUERS:
        raise TwoSessionTailError("two_session_issuer_coverage_invalid")

    summary = {
        "schema": SCHEMA,
        "window_start": days[0],
        "window_end": days[-1],
        "source_api_calls": 4,
        "observed_market_date_pairs": 4,
        "market_source_quality_counts": quality,
        "observed_two_session_issuers": len(factors),
        "per_issuer_partial_factors": factors,
        "source_vendor_bytes_transient": transient_bytes,
        "original_market_json_retained": False,
        "absolute_ohlcv_retained": False,
        "valid_complete_five_day_cohort": False,
        "join_20_60_120_250_allowed": False,
        "corporate_action_and_pit_verified": False,
        "v3_six_pillar_score_certified": False,
    }
    blob = gzip.compress(_canonical(summary), compresslevel=9, mtime=0)
    if not 0 < len(blob) < MAX_FILE_BYTES:
        raise TwoSessionTailError("two_session_private_derived_too_large")
    return summary, blob


def validate(blob, expected):
    try:
        if (type(blob) is not bytes or not 0 < len(blob) < MAX_FILE_BYTES or
            type(expected) not in (tuple, list) or len(expected) != 2):
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as archive:
            raw = archive.read(6_000_001)
        if len(raw) > 6_000_000:
            raise ValueError()
        data = json.loads(raw)
        if (data.get("schema") != SCHEMA or
            data.get("window_start") != expected[0] or
            data.get("window_end") != expected[1] or
            data.get("source_api_calls") != 4 or
            data.get("observed_market_date_pairs") != 4 or
            data.get("original_market_json_retained") is not False or
            data.get("absolute_ohlcv_retained") is not False or
            data.get("valid_complete_five_day_cohort") is not False or
            data.get("join_20_60_120_250_allowed") is not False or
            data.get("v3_six_pillar_score_certified") is not False or
            type(data.get("per_issuer_partial_factors")) is not dict or
            type(data.get("observed_two_session_issuers")) is not int or
            not 0 < data["observed_two_session_issuers"] <= MAX_ISSUERS or
            len(data["per_issuer_partial_factors"]) != data["observed_two_session_issuers"]):
            raise ValueError()
        for key, value in data["per_issuer_partial_factors"].items():
            if (type(key) is not str or
                not re.fullmatch(r"(KOSPI|KOSDAQ):[0-9A-Z]{6}", key) or
                type(value) is not dict or
                value.get("observed_sessions") != 2 or
                value.get("observed_return_intervals") != 1 or
                type(value.get("intrabucket_one_interval_return_bp")) is not int or
                value.get("eligible_for_five_session_chain") is not False or
                value.get("absolute_price_or_volume_retained") is not False):
                raise ValueError()
        if any(x in raw for x in (b'"TDD_CLSPRC"', b'"ACC_TRDVOL"',
                                 b'"raw_source_base64"')):
            raise ValueError()
        return data
    except Exception:
        raise TwoSessionTailError("private_two_session_derived_integrity_failure") from None


def _source_completed(storage, days, checksum):
    saved, _ = storage.get_content(CHECKPOINT)
    manifest = _manifest(saved, days, checksum)
    if manifest["next_window_index"] < len(days) // 5:
        return False
    _verify_last(storage, manifest, days)
    return True


def store(storage, client, days, checksum):
    if storage.repository != PRIVATE_REPO:
        raise TwoSessionTailError("private_derived_destination_required")
    tail = select_tail(days)
    if tail is None:
        return {"status": "no_two_session_remainder", "provider_api_calls": 0,
                "original_source_files_saved": 0}
    storage.verify_private()
    if not _source_completed(storage, days, checksum):
        return {"status": "awaiting_verified_five_session_history",
                "provider_api_calls": 0, "original_source_files_saved": 0}

    path = "derived/partial-tail/" + tail[0] + "_" + tail[1] + ".two-session.json.gz"
    existing, _ = storage.get_content(path)
    if existing is not None:
        known = validate(existing, tail)
        return {"status": "reused_verified_two_session_tail",
                "provider_api_calls": 0,
                "observed_two_session_issuers": known["observed_two_session_issuers"],
                "private_derived_file_bytes": len(existing),
                "original_source_files_saved": 0}

    summary, blob = derive(client, tail)
    storage.put_new(path, blob, "Store KRX 2-session nonreconstructive tail " + tail[1])
    for attempt in range(4):
        received, _ = storage.get_content(path)
        if received == blob:
            verified = validate(received, tail)
            return {"status": "verified_saved_two_session_tail",
                    "dates": tail, "provider_api_calls": 4,
                    "observed_two_session_issuers": verified["observed_two_session_issuers"],
                    "private_derived_file_bytes": len(blob),
                    "transient_provider_bytes": summary["source_vendor_bytes_transient"],
                    "original_source_files_saved": 0,
                    "converts_to_verified_20day": False}
        if attempt < 3:
            time.sleep(.7 * (attempt + 1))
    raise TwoSessionTailError("two_session_private_readback_not_visible")


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO or
        not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "").strip() or
        not os.getenv("KRX_API_KEY", "").strip()):
        print('{"status":"blocked","reason":"derived_private_or_provider_secrets_missing"}')
        return 1
    try:
        from official_probe.clients import KRXClient
        days, digest = history_candidates()
        archive = PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                    os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        result = store(archive, KRXClient(os.environ["KRX_API_KEY"]), days, digest)
        print(json.dumps(result, sort_keys=True))
        return 0
    except TwoSessionTailError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "source_values_exposed": False}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"source_or_private_tail_unavailable","source_values_exposed":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
