"""Publish only aggregate progress from VERIFIED private KRX derived checkpoints.

No raw prices, codes, issuer-period returns, private paths or storage references
are published. This has ZERO KRX/DART API calls; it reads small checkpoints
from the already-authorized PRIVATE GitHub archive via configured credentials.
The rollups are NOT source-adjusted or historical PIT investment certification.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from v3_krx_derived_backfill import (
    history_candidates, _manifest as historical_checkpoint,
    CHECKPOINT as SOURCE_CHECKPOINT, _path as source_path,
)
from v3_krx_derived_5day import validate_derived_blob
from v3_krx_derived_bridges import (
    CHECKPOINT as BRIDGE_CHECKPOINT, _check_progress as bridge_checkpoint,
    _link_path as bridge_path, _read_link as validate_bridge,
)
from v3_krx_derived_rolling_returns import (
    HORIZONS, _progress_path as rolling_path,
    _check_progress as rolling_checkpoint, _file as return_path,
    _validate_rolling as validate_return,
)
from v3_krx_corporate_risk_incremental import (
    CHECKPOINT as RISK_CHECKPOINT, _check_progress as risk_checkpoint,
)
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_dart_bulk_history import write_json

SCHEMA = "v3-verified-private-derived-krx-historical-progress-0.1"
PRIVATE_REPO = "ykvic1/korean-stock-private-data"


class PrivateProgressError(ValueError):
    """Static, non-sensitive error strings only."""


def verified_tail(storage, path, digest):
    """Require last immutable evidence file, not just an optimistic cursor."""
    if not isinstance(digest, str) or len(digest) != 64:
        raise PrivateProgressError("checkpoint_tail_digest_invalid")
    blob, _ = storage.get_content(path)
    if blob is None or sha256(blob).hexdigest() != digest:
        raise PrivateProgressError("private_derived_tail_missing_or_changed")
    return blob


def collect(storage, dates, digest):
    if (storage.repository != PRIVATE_REPO
        or not isinstance(dates, list)
        or not 10 <= len(dates) <= 6000
        or dates != sorted(set(dates))
        or not isinstance(digest, str) or len(digest) != 64):
        raise PrivateProgressError("invalid_private_progress_source_request")
    storage.verify_private()
    first, first_sha = storage.get_content(SOURCE_CHECKPOINT)
    if first is None:
        raise PrivateProgressError("missing_private_derived_source_checkpoint")
    try:
        source = historical_checkpoint(first, dates, digest)
    except Exception:
        raise PrivateProgressError("invalid_private_derived_source_checkpoint") from None
    total = len(dates) // 5
    source_count = source["next_window_index"]
    if source_count > total or source["raw_source_file_count"] != 0:
        raise PrivateProgressError("source_progress_exceeds_calendar_or_keeps_raw")
    if source_count:
        tail = dates[5*(source_count-1):5*source_count]
        blob = verified_tail(storage, source_path(tail),
                             source["last_window_file_sha256"])
        try:
            validate_derived_blob(blob, tail[0], tail[-1])
        except Exception:
            raise PrivateProgressError("invalid_private_source_tail_factor") from None

    bridge_raw, bridge_sha = storage.get_content(BRIDGE_CHECKPOINT)
    try:
        bridge = bridge_checkpoint(bridge_raw, dates, digest, source_count)
    except Exception:
        raise PrivateProgressError("invalid_private_boundary_checkpoint") from None
    linked = bridge["next_boundary_index"] - 1
    if linked < 0 or linked > max(0, source_count-1):
        raise PrivateProgressError("boundary_progress_exceeds_source")
    if linked:
        older, newer = dates[5*linked-1], dates[5*linked]
        blob = verified_tail(storage, bridge_path(older, newer),
                             bridge["last_boundary_sha256"])
        try:
            validate_bridge(blob, older, newer)
        except Exception:
            raise PrivateProgressError("invalid_private_bridge_tail") from None

    # A complete N-session return needs its first N/5 bucket factors and all
    # internal cross-cohort boundaries. "windows" counts ending cohorts, not
    # issuers, and does not certify adjusted-price or historical PIT returns.
    end_available = min(source_count, bridge["next_boundary_index"])
    research_windows = {}
    for horizon in HORIZONS:
        original, _ = storage.get_content(rolling_path(horizon))
        try:
            p = rolling_checkpoint(original, digest, horizon)
        except Exception:
            raise PrivateProgressError("invalid_private_rolling_checkpoint") from None
        b = horizon // 5
        saved = p["next_end_bucket"]-b
        feasible = max(0, end_available-b+1)
        if saved < 0 or saved > feasible or p["vendor_raw_file_count"] != 0:
            raise PrivateProgressError("rolling_progress_exceeds_verified_boundary")
        if saved:
            end = p["next_end_bucket"]-1
            start_day, end_day = dates[5*(end-b)], dates[5*end-1]
            blob = verified_tail(storage, return_path(start_day, end_day, horizon),
                                 p["last_window_sha256"])
            try:
                validate_return(blob, horizon, start_day, end_day)
            except Exception:
                raise PrivateProgressError("invalid_private_rolling_tail") from None
        research_windows[str(horizon)] = {
            "complete_cohort_windows": saved,
            "possible_windows_from_current_verified_bridges": feasible,
            "backlog_windows_at_this_snapshot": feasible-saved,
            "unadjusted_price_return_approved": False,
        }

    risk_raw, _ = storage.get_content(RISK_CHECKPOINT)
    try:
        risk = risk_checkpoint(risk_raw, dates, digest, source_count)
    except Exception:
        raise PrivateProgressError("invalid_private_risk_checkpoint") from None
    reviewed = risk["next_cohort_index"] if risk else 0
    if reviewed > end_available:
        raise PrivateProgressError("risk_review_exceeds_verified_boundary")
    if risk:
        verified_tail(storage, risk["last_audit_file"], risk["last_audit_sha256"])
        if risk["raw_provider_files_stored"] != 0:
            raise PrivateProgressError("risk_checkpoint_retains_raw_market_data")

    # Read checkpoints again; abort stale aggregate publication during a
    # concurrent private checkpoint update. Never write to the private repo.
    second, second_sha = storage.get_content(SOURCE_CHECKPOINT)
    final_bridge, final_bridge_sha = storage.get_content(BRIDGE_CHECKPOINT)
    if (first != second or first_sha != second_sha or
        bridge_raw != final_bridge or bridge_sha != final_bridge_sha):
        raise PrivateProgressError("private_progress_changed_during_read")

    return {
        "schema": SCHEMA,
        "source_candidate_start": dates[0],
        "source_candidate_end": dates[-1],
        "five_session_cohorts_target": total,
        "five_session_cohorts_completed": source_count,
        "five_session_cohorts_remaining": total-source_count,
        "source_history_coverage_ratio": round(source_count/total, 6),
        "last_completed_source_session": (
            dates[5*source_count-1] if source_count else None),
        "cross_cohort_bridges_completed": linked,
        "cross_cohort_bridges_required_for_completed_source": max(0,source_count-1),
        "cross_cohort_bridges_backlog": max(0,source_count-1)-linked,
        "rolling_return_horizon_research": research_windows,
        "price_discontinuity_heuristic_cohorts_reviewed": reviewed,
        "price_discontinuity_review_backlog_at_verified_bridges": end_available-reviewed,
        "private_immutable_evidence_tails_verified": True,
        "private_repo_verified_before_read": True,
        "historical_delisted_universe_pit_verified": False,
        "corporate_actions_or_adjusted_prices_verified": False,
        "official_exchange_historical_factor_certified": False,
        "industry_15_or_other_v3_trade_score_promoted": False,
        "public_issuer_prices_or_individual_returns_written": False,
        "provider_data_new_calls": 0,
        "note": "KRX daily original market source stays out of this public "
                "report. Progress counts represent PRIVATE nonreconstructive "
                "derived research only, not current full-market historical "
                "issuer coverage nor split-adjusted, PIT-verified 100-point "
                "investment scores.",
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs",default="docs")
    parser.add_argument("--mode",choices=("write","verify"),default="verify")
    args=parser.parse_args(argv)
    repo=os.getenv("V3_PRIVATE_ARCHIVE_REPO","")
    token=os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","")
    if repo!=PRIVATE_REPO or len(token)<20:
        raise PrivateProgressError("private_derived_read_secrets_missing")
    dates,digest=history_candidates()
    storage=PrivateGitStorage(repo,token)
    report=collect(storage,dates,digest)
    target=Path(args.docs)/"v3-krx-derived-backfill-progress.json"
    if args.mode=="write":
        write_json(target,report)
    else:
        try:
            recorded=json.loads(target.read_text(encoding="utf8"))
        except Exception:
            raise PrivateProgressError("public_derived_progress_file_missing") from None
        if recorded!=report:
            raise PrivateProgressError("private_progress_changed_since_public_render")
    print(json.dumps({
        "status":"verified_aggregate_only",
        "completed":report["five_session_cohorts_completed"],
        "target":report["five_session_cohorts_target"],
        "bridges":report["cross_cohort_bridges_completed"],
        "rolling_windows":{h:x["complete_cohort_windows"] for h,x
                           in report["rolling_return_horizon_research"].items()},
        "risk_reviewed":report["price_discontinuity_heuristic_cohorts_reviewed"],
        "new_provider_calls":0,
        "full_v3_scores_certified":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
