"""Publish verified aggregate actual KRX trading-money collection progress only.

Only source checkpoint counters and a validated immutable derived tail are read.
No KRX request, per-issuer amount, stock code, private path or prices are public.
Recent-first windows are research, not regular-session/PIT score certification.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from v3_krx_money_backfill import (
    CHECKPOINT, _manifest, _cohort_at_recent_first, _path,
    history_candidates,
)
from v3_krx_money_5day import validate_money_blob
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA = "v3-public-krx-actual-money-derived-progress-0.1"
PRIVATE_REPO = "ykvic1/korean-stock-private-data"
HORIZONS = (20, 60, 120, 240)


class MoneyProgressError(ValueError):
    """Fixed error only: do not expose provider/private-origin details."""


def collect(storage, dates, digest):
    if (storage.repository != PRIVATE_REPO or not isinstance(dates, list)
        or not 5 <= len(dates) <= 6000 or dates != sorted(set(dates))
        or type(digest) is not str or len(digest) != 64):
        raise MoneyProgressError("invalid_public_money_progress_request")
    storage.verify_private()
    original, original_sha = storage.get_content(CHECKPOINT)
    if original is None:
        raise MoneyProgressError("private_money_checkpoint_unavailable")
    try:
        checkpoint = _manifest(original, dates, digest)
    except Exception:
        raise MoneyProgressError("private_money_checkpoint_invalid") from None
    total = len(dates) // 5
    count = checkpoint["next_window_index"]
    if count > total or checkpoint["raw_source_file_count"] != 0:
        raise MoneyProgressError("private_money_checkpoint_out_of_range")
    oldest_complete_end = None
    if count:
        tail_dates = _cohort_at_recent_first(dates, count - 1)
        blob, _ = storage.get_content(_path(tail_dates))
        if (type(blob) is not bytes
            or sha256(blob).hexdigest() != checkpoint["last_window_file_sha256"]):
            raise MoneyProgressError("private_money_checkpoint_tail_missing_or_changed")
        try:
            validated = validate_money_blob(blob, tail_dates[0], tail_dates[-1])
        except Exception:
            raise MoneyProgressError("private_money_checkpoint_tail_invalid") from None
        if (validated["complete_five_session_issuer_market_pairs"] < 1
            or validated["raw_vendor_source_retained"] is not False):
            raise MoneyProgressError("private_money_checkpoint_tail_not_derived_only")
        oldest_complete_end = tail_dates[-1]
    again, again_sha = storage.get_content(CHECKPOINT)
    if original != again or original_sha != again_sha:
        raise MoneyProgressError("private_money_checkpoint_changed_during_verification")
    windows = {
        str(h): {
            # Source cohorts may overlap the horizon, but source coverage is
            # not the same as each issuer's complete observation or certified OHLCV.
            "possible_consecutive_five_session_endpoints": max(0, count - h // 5 + 1),
            "full_source_cohorts_available": count >= h // 5,
            "each_issuer_history_complete": False,
            "investment_score_allowed": False,
        }
        for h in HORIZONS
    }
    return {
        "schema": SCHEMA,
        "collection_order": "latest_first",
        "collection_source": "actual_KRX_ACC_TRDVAL_derived_private",
        "five_session_cohorts_completed": count,
        "five_session_cohorts_target": total,
        "five_session_cohorts_remaining": total - count,
        "candidate_sessions_in_completed_cohorts": count * 5,
        "candidate_sessions_target": len(dates),
        "derived_issuer_cohort_records": checkpoint["full_issuer_bucket_records"],
        "private_derived_compressed_bytes": checkpoint["compressed_factor_bytes"],
        "oldest_completed_cohort_end": oldest_complete_end,
        "daily_horizon_research": windows,
        "5_10_20_60_120_five_session_buckets_are_exact_calendar_weeks": False,
        "exact_daily_money_obv_proven": False,
        "original_krx_ohlcv_verified_regular_session_only": False,
        "corporate_actions_and_historical_issuer_pit_verified": False,
        "private_immutable_checkpoint_tail_verified": True,
        "raw_krx_files_saved": 0,
        "new_krx_api_calls": 0,
        "money_factors_in_final_investment_scores": False,
        "full_v3_score_certified": False,
        "note": "Aggregate research coverage, not issuer-complete multi-horizon "
                "liquidity, regular-session-only data, exact daily OBV, or "
                "tradeable 100-point score certification.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", default="docs")
    parser.add_argument("--mode", choices=("verify", "write"), default="verify")
    args = parser.parse_args(argv)
    repo = os.getenv("V3_PRIVATE_ARCHIVE_REPO", "")
    token = os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")
    if repo != PRIVATE_REPO or len(token) < 20:
        raise MoneyProgressError("private_money_read_secrets_missing")
    dates, digest = history_candidates()
    result = collect(PrivateGitStorage(repo, token), dates, digest)
    target = Path(args.docs) / "v3-krx-money-derived-progress.json"
    if args.mode == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                     indent=2) + "\n", encoding="utf8")
    else:
        try:
            current = json.loads(target.read_text(encoding="utf8"))
        except Exception:
            raise MoneyProgressError("published_money_progress_not_found") from None
        if current != result:
            raise MoneyProgressError("published_money_progress_stale_or_mismatched")
    print(json.dumps({
        "status": "verified_public_money_progress_only",
        "completed": result["five_session_cohorts_completed"],
        "target": result["five_session_cohorts_target"],
        "new_krx_api_calls": 0,
        "full_v3_score_certified": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
