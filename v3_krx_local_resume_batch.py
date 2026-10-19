"""Single bounded local KRX batch with resume and private append-only storage.

DEFAULT IS ZERO NETWORK CALLS. Live opt-in requires:
--live --ack-private-storage --private-db <path OUTSIDE repo>
and KRX_API_KEY from the environment. Never execute in public GitHub Actions,
upload raw vendor bytes, generate investor-flow or mark buy recommendations.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from v3_krx_private_backfill_plan import plan_private_history, BackfillPlanError
from v3_krx_history_collection import collect_krx_history, HistoryCollectionError
from v3_local_private_store import archived_trade_dates, PrivateStoreError


class LocalBatchError(ValueError):
    """Only sanitized static reason codes may escape."""


def run_one_local_batch(*, start, end, database_path, batch_size=5, live=False,
                        consent=False, key=None, client=None,
                        calendar_source="weekdays"):
    if (live is True and consent is not True):
        raise LocalBatchError("private_storage_contract_ack_required")
    if live is True and (not isinstance(database_path, (str, Path))
                         or not str(database_path)):
        raise LocalBatchError("explicit_private_database_path_required")
    if live is True:
        # Fail BEFORE any vendor request if the proposed durable destination
        # is inside the public GitHub repository, a link or a wrong extension.
        path = Path(database_path).expanduser()
        repo_root = Path(__file__).resolve().parent
        absolute = path.resolve()
        if (path.suffix != ".sqlite3" or path.is_symlink()
                or path.parent.is_symlink()
                or absolute == repo_root or repo_root in absolute.parents):
            raise LocalBatchError("private_storage_path_not_safe")
    try:
        plan=plan_private_history(start,end,db_path=database_path,
                                  batch_size=batch_size,
                                  calendar_source=calendar_source)
    except BackfillPlanError:
        raise LocalBatchError("verified_private_resume_plan_rejected") from None
    dates=plan["next_batch_dates"]
    if live is not True:
        return {**plan,"mode":"dry_run","actual_krx_calls":0,
                "live_storage_permission_approved":False}
    if not dates:
        return {**plan,"mode":"live_no_missing_candidate_dates",
                "actual_krx_calls":0,"private_archive_updated":False}
    if type(key) is not str or not key.strip():
        raise LocalBatchError("krx_api_key_environment_missing")
    # A valid SHA chain does not prove the source JSON contains the stated
    # market/date or usable OHLCV. Before ANY additional official request,
    # independently re-check all existing private pairs without rewriting.
    if plan["archived_two_market_pairs_verified_internally"]:
        from v3_krx_archive_reaudit import audit_private_archive, ArchiveReauditError
        try:
            audit_private_archive(
                start=start, end=end, private_db=database_path,
                batch_size=batch_size, calendar_source=calendar_source)
        except ArchiveReauditError:
            raise LocalBatchError(
                "existing_private_archive_semantic_reaudit_failed") from None
    try:
        collected=collect_krx_history(
            dates,live=True,key=key,client=client,
            archive_path=database_path,consent_to_private_retention=True)
        archived=archived_trade_dates(database_path=database_path)
    except (HistoryCollectionError, PrivateStoreError):
        # May have safely saved earlier completed pairs, never falsely report
        # successful complete batch or echo a response/credential.
        raise LocalBatchError("local_batch_incomplete_inspect_archive_before_retry") from None
    if not (len(archived) == plan["archived_two_market_pairs_verified_internally"]+len(dates)
            and archived[-len(dates):] == dates):
        raise LocalBatchError("postcommit_private_archive_dates_disagree")
    return {
        "schema":"v3-krx-verified-private-resume-batch-0.1",
        "mode":"local_live",
        "attempted_market_calls_at_most":len(dates)*2,
        "new_paired_days_observed":collected["observed_days"],
        "total_archive_paired_days_integrity_checked":len(archived),
        "calendar_candidate_source":plan["calendar_candidate_source"],
        "calendar_candidate_library_version":plan["calendar_candidate_library_version"],
        "candidate_dates_sha256":plan["candidate_dates_sha256"],
        "official_krx_session_calendar_authenticated":False,
        "latest_paired_day":archived[-1],
        "historical_exchange_calendar_verified":False,
        "historical_issuer_universe_verified":False,
        "adjusted_price_events_verified":False,
        "regular_only_volume_verified":False,
        "private_archive_independently_anchored":False,
        "v3_release_ready":False,"confirmed_buy":False,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="LOCAL only. Each live run stores at most 5 complete KRX market-pair days.")
    parser.add_argument("--start",required=True)
    parser.add_argument("--end",required=True)
    parser.add_argument("--private-db",help="Explicit local SQLite outside public repository")
    parser.add_argument("--batch-size",type=int,default=5)
    parser.add_argument("--calendar-source",choices=("weekdays","xkrx"),
                        default="weekdays",help="XKRX is an unverified third-party candidate calendar")
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--ack-private-storage",action="store_true")
    args=parser.parse_args(argv)
    try:
        # No implicit DB path for a live operation.
        if not args.private_db:
            raise LocalBatchError("explicit_private_database_path_required")
        report=run_one_local_batch(
            start=args.start,end=args.end,database_path=args.private_db,
            batch_size=args.batch_size,live=args.live,
            consent=args.ack_private_storage,
            calendar_source=args.calendar_source,
            key=os.environ.get("KRX_API_KEY"))
        print(json.dumps(report,sort_keys=True))
        return 0
    except LocalBatchError as err:
        print(json.dumps({
            "status":"blocked","reason":str(err),
            "v3_release_ready":False,"confirmed_buy":False},sort_keys=True))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
