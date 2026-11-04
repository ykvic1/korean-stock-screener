"""Resumable, long-running OpenDART receipt catch-up; no score promotion.

Each segment uses the existing 72-call, immutable private page and CAS-cursor
collector. A single operator action may advance many segments, but a hard
provider-call/time budget and the shared Actions lock protect source integrity.
"""
from __future__ import annotations

import json
import os
import time

from v3_dart_historical_event_receipts import (
    DARTEventReceiptError,
    MAX_API_CALLS,
    MONTHS,
    PRIVATE_REPO,
    official_fetch,
    public_failure_code,
    run,
)
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError

MAX_BATCHES = 50
MAX_PROVIDER_CALLS = MAX_BATCHES * MAX_API_CALLS
MAX_SECONDS = 5 * 60 * 60


class QuotaWaitingPrivateGitStorage(PrivateGitStorage):
    """Resume a rejected GET after a bounded quota cooldown, within this run.

    PUTs retain the transport's fail-fast contract: never replay a possibly
    committed write. The next run reconciles its immutable page and CAS cursor.
    Permission errors, source corruption and unclassified failures still stop.
    """
    def __init__(self, repository, token, *, deadline, clock=None, sleeper=None):
        super().__init__(repository, token)
        self.clock = time.monotonic if clock is None else clock
        self.sleeper = time.sleep if sleeper is None else sleeper
        self.deadline = deadline
        self.quota_pauses = 0

    def _http(self, method, path, body=None):
        while True:
            try:
                return super()._http(method, path, body)
            except PrivateHistoryError as exc:
                delay = getattr(exc, "safe_retry_after_seconds", None)
                if (method != "GET" or type(exc) is not PrivateHistoryError
                    or exc.args != ("private_storage_api_failed",)
                    or getattr(exc, "safe_http_status", None) not in
                        ("http_403_rate_limited", "http_429")
                    or type(delay) is not int or not 5 <= delay <= 3605
                    or self.quota_pauses >= 3
                    or self.clock() + delay + 30 >= self.deadline):
                    raise
                self.quota_pauses += 1
                print("private_github_read_quota_cooldown", flush=True)
                until = self.clock() + delay
                while True:
                    remaining = until - self.clock()
                    if remaining <= 0:
                        break
                    self.sleeper(min(30, remaining))
                # Retry only this rejected read. No provider fetch, new page,
                # checkpoint write or segment accounting is replayed here.


def continuous_catchup(collect, *, clock=None, max_batches=MAX_BATCHES,
                       max_provider_calls=MAX_PROVIDER_CALLS,
                       max_seconds=MAX_SECONDS):
    """Continue bounded private checkpoints until complete or budget exhausted.

    The callback accepts a per-segment request ceiling (never over 72) and
    returns only the anonymous integer summary from the existing collector.
    """
    if (not callable(collect)
        or type(max_batches) is not int or not 1 <= max_batches <= MAX_BATCHES
        or type(max_provider_calls) is not int
        or not 1 <= max_provider_calls <= MAX_PROVIDER_CALLS
        or type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS):
        raise DARTEventReceiptError("untrusted_private_dart_collection_request")
    clock = time.monotonic if clock is None else clock
    begin = clock()
    calls = pages = batches = reused = 0
    completed_months = None
    reason = "batch_limit"
    for number in range(max_batches):
        if clock() - begin >= max_seconds:
            reason = "time_limit"
            break
        if calls >= max_provider_calls:
            reason = "provider_budget"
            break
        ceiling = min(MAX_API_CALLS, max_provider_calls - calls)
        stats = collect(ceiling)
        if (type(stats) is not dict or
            any(type(stats.get(key)) is not int for key in
                ("provider_calls", "new_pages", "previously_saved_pages_reused",
                 "months_completed")) or
            not 0 <= stats["provider_calls"] <= ceiling or
            not 0 <= stats["new_pages"] <= stats["provider_calls"] or
            stats["previously_saved_pages_reused"] < 0 or
            not 0 <= stats["months_completed"] <= len(MONTHS) or
            (completed_months is not None and
             stats["months_completed"] < completed_months)):
            raise DARTEventReceiptError("private_dart_cursor_readback_failed")
        calls += stats["provider_calls"]
        pages += stats["new_pages"]
        reused += stats["previously_saved_pages_reused"]
        batches += 1
        completed_months = stats["months_completed"]
        print(json.dumps({"catchup_segment": number + 1,
                          "months_completed": completed_months,
                          "provider_calls_this_segment": stats["provider_calls"],
                          "pages_saved_this_segment": stats["new_pages"]},
                         sort_keys=True), flush=True)
        if completed_months == len(MONTHS):
            reason = "complete"
            break
        if stats["provider_calls"] == stats["new_pages"] == 0 and (
                stats["previously_saved_pages_reused"] == 0):
            reason = "stalled_no_source_progress"
            break
    return {"status": reason, "months_completed": completed_months,
            "months_total": len(MONTHS), "provider_calls": calls,
            "new_pages": pages, "orphan_pages_recovered": reused,
            "segments": batches,
            "source_effective_dates_verified": False,
            "historical_pit_certified": False,
            "investment_scores_promoted": False}


def main():
    if (os.environ.get("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.environ.get("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20
        or len(os.environ.get("DART_API_KEY", "")) != 40):
        raise SystemExit("blocked: required private DART research credentials absent")
    storage = QuotaWaitingPrivateGitStorage(
        PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"],
        deadline=time.monotonic() + MAX_SECONDS)
    key = os.environ["DART_API_KEY"]
    try:
        summary = continuous_catchup(
            lambda ceiling: run(
                storage, lambda m, c, p: official_fetch(key, m, c, p),
                max_calls=ceiling))
    except Exception as error:
        raise SystemExit("blocked: " + public_failure_code(error)) from None
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
