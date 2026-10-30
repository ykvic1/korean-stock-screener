"""Public anonymous progress counters from the PRIVATE OpenDART receipt cursor.

This is a receipt-metadata collection *progress* report, not a complete archive
audit, independent corporate-action proof, PIT history or adjusted-price data.
Only one private immutable tail and an unchanged checkpoint are verified.
No identifier, filing title, private path, market price or provider credential
is emitted to the public report or stdout. ZERO DART/KRX provider API requests.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time

from v3_dart_historical_event_receipts import (
    CATEGORIES, CHECKPOINT, MONTHS, PRIVATE_REPO, _check_cursor, _decode, _path,
)
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_dart_bulk_history import write_json

SCHEMA = "v3-public-dart-historical-receipt-progress-v1"
OUTPUT = "v3-dart-historical-receipt-progress.json"


class DARTProgressError(ValueError):
    """Static, privacy-safe failure code; never embeds source data."""


def _previous_location(cursor):
    """Expected tail partition and page relationship for the next cursor."""
    mi, ci, pn = (cursor["month_index"], cursor["category_index"],
                  cursor["page"])
    if pn > 1:
        if mi >= len(MONTHS):
            raise DARTProgressError("invalid_private_dart_cursor_position")
        return MONTHS[mi], CATEGORIES[ci], pn - 1, False
    if ci > 0:
        return MONTHS[mi], CATEGORIES[ci - 1], None, True
    if mi > 0:
        return MONTHS[mi - 1], CATEGORIES[-1], None, True
    return None


def collect(storage):
    if storage.repository != PRIVATE_REPO:
        raise DARTProgressError("untrusted_private_dart_progress_source")
    storage.verify_private()
    raw, original_sha = storage.get_content(CHECKPOINT)
    if raw is None:
        raise DARTProgressError("missing_private_dart_receipt_checkpoint")
    try:
        cursor = _check_cursor(raw)
    except Exception:
        raise DARTProgressError("invalid_private_dart_receipt_checkpoint") from None

    mi, ci, pn = (cursor["month_index"], cursor["category_index"],
                  cursor["page"])
    pages, receipts = cursor["saved_pages"], cursor["saved_receipts"]
    lower_bound = mi * len(CATEGORIES) + ci + pn - 1
    if (pages < lower_bound or receipts > pages * 100
        or (pages == 0) != (mi == 0 and ci == 0 and pn == 1)):
        raise DARTProgressError("inconsistent_private_dart_receipt_progress")

    previous = _previous_location(cursor)
    if previous is None:
        if cursor["last_page_file"] is not None:
            raise DARTProgressError("unexpected_private_dart_progress_tail")
    else:
        month, category, exact_page, must_be_final = previous
        last_file = cursor["last_page_file"]
        partition = re.escape("derived/dart-filing-receipts/" +
                              month + "/" + category + "/")
        match = (re.fullmatch(partition + r"([0-9]{4,6})\.json\.gz",
                              last_file) if type(last_file) is str else None)
        if match is None:
            raise DARTProgressError("private_dart_progress_tail_wrong_partition")
        tail_page = int(match.group(1))
        if (tail_page < 1 or tail_page > 100_000
            or exact_page is not None and tail_page != exact_page
            or _path(month, category, tail_page) != last_file):
            raise DARTProgressError("private_dart_progress_tail_out_of_order")
        blob, _ = storage.get_content(last_file)
        if (blob is None
            or sha256(blob).hexdigest() != cursor["last_page_sha256"]):
            raise DARTProgressError("private_dart_progress_tail_changed")
        try:
            tail = _decode(blob, month, category, tail_page)
        except Exception:
            raise DARTProgressError("invalid_private_dart_progress_tail") from None
        if (must_be_final and not
                (tail["total_pages"] == 0 and tail_page == 1
                 or tail_page == tail["total_pages"])
            or not must_be_final and tail_page >= tail["total_pages"]):
            raise DARTProgressError("private_dart_progress_cursor_not_contiguous")

    observed, observed_sha = storage.get_content(CHECKPOINT)
    if raw != observed or original_sha != observed_sha:
        raise DARTProgressError("private_dart_progress_changed_during_read")

    return {
        "schema": SCHEMA,
        "filing_months_total": len(MONTHS),
        "filing_months_checkpointed": mi,
        "filing_months_remaining": len(MONTHS) - mi,
        "filing_months_checkpointed_ratio": round(mi / len(MONTHS), 6),
        "last_checkpointed_filing_month": MONTHS[mi - 1] if mi else None,
        "current_collection_filing_month": MONTHS[mi] if mi < len(MONTHS) else None,
        "private_receipt_pages_checkpointed": pages,
        "private_filing_receipts_checkpointed": receipts,
        "filing_category_scope": list(CATEGORIES),
        "private_tail_digest_and_schema_verified": previous is not None,
        "private_checkpoint_stable_during_read": True,
        "full_historical_archive_independently_audited": False,
        "corporate_actions_or_effective_dates_certified": False,
        "historical_issuer_universe_pit_certified": False,
        "historical_adjusted_prices_certified": False,
        "v3_trade_scores_promoted": False,
        "public_issuer_receipts_or_source_raw_published": False,
        "provider_api_calls": 0,
        "note": "Anonymous private OpenDART B/C/E/I filing-receipt collection "
                "counts only. Page metadata is not a signed archival snapshot; "
                "month progress is NOT independent evidence of historical "
                "corporate actions, adjusted closes, delisted issuers, PIT "
                "values or investment-score certification.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="docs")
    parser.add_argument("--mode", choices=("write", "verify"), default="verify")
    args = parser.parse_args(argv)
    repo = os.environ.get("V3_PRIVATE_ARCHIVE_REPO", "")
    token = os.environ.get("V3_PRIVATE_ARCHIVE_TOKEN", "")
    if repo != PRIVATE_REPO or len(token) < 20:
        raise DARTProgressError("private_dart_progress_secrets_missing")
    storage = PrivateGitStorage(repo, token)
    for attempt in range(3):
        try:
            report = collect(storage)
            break
        except DARTProgressError as exc:
            if str(exc) != "private_dart_progress_changed_during_read" or attempt == 2:
                raise
            time.sleep(attempt + 1)
    target = Path(args.docs) / OUTPUT
    if args.mode == "write":
        write_json(target, report)
    else:
        try:
            published = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            raise DARTProgressError("public_dart_progress_report_missing") from None
        if published != report:
            raise DARTProgressError("public_dart_progress_report_stale")
    print(json.dumps({
        "status": "private_dart_aggregate_progress_only",
        "months_checkpointed": report["filing_months_checkpointed"],
        "months_total": report["filing_months_total"],
        "receipt_pages": report["private_receipt_pages_checkpointed"],
        "new_provider_calls": 0,
        "trade_scores_certified": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
