"""Additive PRIVATE OpenDART action-category receipt evidence, never trading proof.

The original three-endpoint receipt ledger is immutable. This separate cursor
queries only additional official decision endpoints appropriate to previously
verified title-hint CLASSES. A title hint is routing only, never event proof.
No ZIP content, response rows, price, member path or issuer identity is public.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
import re
import time

from v3_dart_historical_event_receipts import MONTHS, PRIVATE_REPO, _bytes as canonical, _dates
from v3_private_dart_krx_filing_title_triage import (
    CHECKPOINT as TRIAGE_CHECKPOINT, _cursor as triage_cursor, _path as triage_path,
)
from v3_private_dart_original_evidence import _candidate_queue
from v3_private_dart_structured_action_receipts import (
    StructuredActionError, _same_bytes, _put_immutable_report_with_retry,
    official_fetch, inspect_response, SUPPLEMENTAL_ENDPOINTS,
    _valid_checkpoint_report_pointer, _valid_schedule_candidates,
)
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError

SCHEMA = "v3-private-dart-structured-action-extension-v1"
BASE = "derived/dart-structured-action-extension"
CHECKPOINT = BASE + "/checkpoint.json"
# At most 4 receipts x 5 endpoints x 30s provider timeout = 10 minutes,
# leaving time for private checkpoint writes in the 15-minute workflow.
MAX_CANDIDATES = 4
MAX_PROVIDER_CALLS = MAX_CANDIDATES * len(SUPPLEMENTAL_ENDPOINTS)
SHA = re.compile(r"[0-9a-f]{64}\Z")

# Official endpoints verified against FSS's published major-event API guide.
# Stock split/consolidation and stock dividend do NOT get guessed endpoints.
HINT_ENDPOINTS = {
    "rights_issue": ("piicDecsn", "pifricDecsn"),
    "bonus_issue": ("pifricDecsn",),
    "capital_reduction": ("crDecsn",),
    "merger_split": ("cmpDvmgDecsn",),
    "share_exchange": ("stkExtrDecsn",),
}


class ExtensionError(ValueError):
    """Fixed privacy-safe error reasons only."""


def endpoints_for_hints(hints):
    if (type(hints) is not list or not hints or
        any(type(h) is not str or len(h) > 60 for h in hints)):
        raise ExtensionError("invalid_private_action_hints")
    selected = set()
    for hint in hints:
        selected.update(HINT_ENDPOINTS.get(hint, ()))
    return tuple(ep for ep in SUPPLEMENTAL_ENDPOINTS if ep in selected)


def _report_path(index, receipt):
    return (BASE + "/reports/" + MONTHS[index] + "--" + MONTHS[index+1]
            + "/" + receipt + ".json")


def _empty():
    return {
        "schema": SCHEMA, "next_pair_index": 0, "next_candidate_index": 0,
        "queue_sha256": None, "last_report_file": None,
        "last_report_sha256": None,
        "effective_dates_certified": False, "adjusted_prices_authorized": False,
        "historical_issuer_identity_certified": False, "scores_authorized": False,
    }


def _cursor(raw):
    if raw is None:
        return _empty()
    try:
        x = json.loads(raw)
        i, j = x.get("next_pair_index"), x.get("next_candidate_index")
        q, p, h = x.get("queue_sha256"), x.get("last_report_file"), x.get("last_report_sha256")
        if (x.get("schema") != SCHEMA or type(i) is not int or
            not 0 <= i <= len(MONTHS)-1 or type(j) is not int or
            not 0 <= j <= 2000 or
            (j == 0 and q is not None) or
            (j > 0 and (type(q) is not str or not SHA.fullmatch(q))) or
            not _valid_checkpoint_report_pointer(i, j, p, h, BASE) or
            any(x.get(flag) is not False for flag in (
                "effective_dates_certified", "adjusted_prices_authorized",
                "historical_issuer_identity_certified", "scores_authorized"))):
            raise ValueError()
        return x
    except Exception:
        raise ExtensionError("invalid_private_extension_checkpoint") from None


def _report(index, record, results):
    selected = endpoints_for_hints(record["title_hints"])
    return {
        "schema": SCHEMA,
        "source_pair": MONTHS[index] + "--" + MONTHS[index+1],
        "rcept_no": record["rcept_no"], "corp_code": record["corp_code"],
        "title_hints": record["title_hints"],
        "queried_endpoint_scope": list(selected), "endpoint_results": results,
        "exact_receipt_event_categories": [r["endpoint"] for r in results
                                           if r["exact_receipt_match"]],
        "title_hints_without_endpoint": sorted(
            h for h in record["title_hints"] if h not in HINT_ENDPOINTS),
        "complete_corporate_action_universe_checked": False,
        "effective_dates_certified": False, "adjusted_prices_authorized": False,
        "historical_issuer_identity_certified": False,
        "source_zip_retained": False, "raw_provider_response_retained": False,
        "scores_authorized": False,
    }


def _validate_report(raw, index, record):
    try:
        if type(raw) is not bytes or not 0 < len(raw) < 80_000:
            raise ValueError()
        obj = json.loads(raw)
        scope = endpoints_for_hints(record["title_hints"])
        if (obj.get("schema") != SCHEMA or
            obj.get("source_pair") != MONTHS[index] + "--" + MONTHS[index+1] or
            obj.get("rcept_no") != record["rcept_no"] or
            obj.get("corp_code") != record["corp_code"] or
            obj.get("title_hints") != record["title_hints"] or
            obj.get("queried_endpoint_scope") != list(scope) or
            obj.get("title_hints_without_endpoint") != sorted(
                h for h in record["title_hints"] if h not in HINT_ENDPOINTS) or
            any(obj.get(flag) is not False for flag in (
                "complete_corporate_action_universe_checked", "effective_dates_certified",
                "adjusted_prices_authorized", "historical_issuer_identity_certified",
                "source_zip_retained", "raw_provider_response_retained", "scores_authorized"))):
            raise ValueError()
        rows = obj.get("endpoint_results")
        if type(rows) is not list or len(rows) != len(scope):
            raise ValueError()
        matched = []
        for expected, row in zip(scope, rows):
            if (type(row) is not dict or row.get("endpoint") != expected or
                row.get("provider_status") not in ("000", "013") or
                type(row.get("list_rows")) is not int or
                not 0 <= row["list_rows"] <= 200 or
                type(row.get("exact_receipt_match")) is not bool or
                (row["provider_status"] == "013" and
                 (row["list_rows"] != 0 or row["exact_receipt_match"])) or
                (row["exact_receipt_match"] and row["list_rows"] == 0) or
                not _valid_schedule_candidates(row, expected)):
                raise ValueError()
            if row["exact_receipt_match"]:
                matched.append(expected)
        if obj.get("exact_receipt_event_categories") != matched:
            raise ValueError()
        return obj
    except Exception:
        raise ExtensionError("invalid_private_extension_report") from None


def run(storage, fetch, limit=MAX_CANDIDATES):
    if (getattr(storage, "repository", None) != PRIVATE_REPO or
        type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES):
        raise ExtensionError("invalid_private_extension_destination")
    storage.verify_private()
    triage_raw, _ = storage.get_content(TRIAGE_CHECKPOINT)
    try:
        triage = triage_cursor(triage_raw)
    except Exception:
        raise ExtensionError("invalid_private_extension_source_cursor") from None
    cursor_raw, cursor_sha = storage.get_content(CHECKPOINT)
    state = _cursor(cursor_raw)
    if state["last_report_file"]:
        previous, _ = storage.get_content(state["last_report_file"])
        if previous is None or sha256(previous).hexdigest() != state["last_report_sha256"]:
            raise ExtensionError("private_extension_tail_changed")
    i = state["next_pair_index"]
    if i >= triage["next_pair_index"]:
        return {"status": "waiting_for_private_title_queue", "provider_calls": 0,
                "processed_candidates": 0, "scores_promoted": 0}
    queue_raw, _ = storage.get_content(triage_path(i))
    queue, digest, _ = _candidate_queue(queue_raw, i)
    j = state["next_candidate_index"]
    if j > len(queue) or (j and state["queue_sha256"] != digest):
        raise ExtensionError("private_extension_queue_changed")
    if j and state["last_report_file"] != _report_path(i, queue[j-1]["rcept_no"]):
        raise ExtensionError("private_extension_checkpoint_queue_tail_mismatch")
    start = _dates(MONTHS[i])[0]
    end = _dates(MONTHS[i+1])[1]
    calls = advanced = reused = matched = unqueried = schedule_candidates = 0
    last_path, last_digest = state["last_report_file"], state["last_report_sha256"]
    while j < len(queue) and advanced < limit:
        record = queue[j]
        path = _report_path(i, record["rcept_no"])
        existing, _ = storage.get_content(path)
        if existing is None:
            observations = []
            for endpoint in endpoints_for_hints(record["title_hints"]):
                if calls >= MAX_PROVIDER_CALLS:
                    raise ExtensionError("private_extension_provider_budget_exceeded")
                response = fetch(endpoint, record["corp_code"], start, end)
                calls += 1
                observations.append(inspect_response(
                    response, endpoint, record["corp_code"], record["rcept_no"]))
            encoded = canonical(_report(i, record, observations))
            try:
                # Keep all official responses in memory during a transient Git
                # conflict: a failed/ambiguous private PUT must not cause a
                # second DART request for the same receipt in this job.
                _put_immutable_report_with_retry(storage, path, encoded)
            except PrivateHistoryError as exc:
                if str(exc) == "private_storage_api_failed":
                    # Even after bounded retries fail, do NOT allow main()'s
                    # whole-run retry to exceed this job's provider budget.
                    raise ExtensionError("private_extension_immutable_write_unavailable") from None
                raise
            _same_bytes(storage, path, encoded)
        else:
            encoded = existing
            reused += 1
        checked = _validate_report(encoded, i, record)
        matched += int(bool(checked["exact_receipt_event_categories"]))
        schedule_candidates += sum(
            len(row.get("reported_schedule_date_candidates", []))
            for row in checked["endpoint_results"])
        unqueried += int(not checked["queried_endpoint_scope"])
        last_path, last_digest = path, sha256(encoded).hexdigest()
        j += 1
        advanced += 1
    pair_end_index = j
    pair_remaining = len(queue) - pair_end_index
    if j == len(queue):
        i, j, digest = i + 1, 0, None
    if (storage.get_content(TRIAGE_CHECKPOINT)[0] != triage_raw or
        storage.get_content(triage_path(state["next_pair_index"]))[0] != queue_raw):
        raise ExtensionError("private_extension_source_changed")
    data = canonical({**state, "next_pair_index": i, "next_candidate_index": j,
                      "queue_sha256": digest if j else None,
                      "last_report_file": last_path, "last_report_sha256": last_digest})
    if cursor_sha is None:
        storage.put_new(CHECKPOINT, data, "Start supplemental DART action cursor")
    else:
        storage.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance supplemental DART action cursor",
            "content": base64.b64encode(data).decode("ascii"), "sha": cursor_sha,
        })
    _same_bytes(storage, CHECKPOINT, data)
    return {
        "status": "private_structured_action_supplement_completed",
        "processed_candidates": advanced, "provider_calls": calls,
        "previous_reports_reused": reused,
        "new_exact_receipt_category_matches": matched,
        "reported_schedule_date_candidates_observed": schedule_candidates,
        "candidates_without_supplemental_endpoint": unqueried,
        "source_pair_candidate_count": len(queue),
        "source_pair_next_candidate_index": pair_end_index,
        "source_pair_remaining_candidates": pair_remaining,
        "candidate_budget_per_run": MAX_CANDIDATES,
        "provider_call_budget_per_run": MAX_PROVIDER_CALLS,
        "next_pair_index": i, "complete_event_universe_certified": False,
        "corporate_action_effective_dates_certified": False,
        "adjusted_prices_certified": False, "scores_promoted": 0,
        "public_issuer_records_written": 0,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO or
        len(os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20 or
        len(os.getenv("DART_API_KEY", "")) != 40):
        print('{"status":"blocked","reason":"private_extension_credentials_missing"}')
        return 1
    try:
        storage = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        key = os.environ["DART_API_KEY"]
        for attempt in range(3):
            try:
                result = run(storage, lambda ep, corp, start, end:
                             official_fetch(key, ep, corp, start, end))
                print(json.dumps(result, sort_keys=True))
                return 0
            except PrivateHistoryError as exc:
                # A provider/network error can happen AFTER a private write.
                # Re-read the checkpoint and immutable report in run() before
                # retrying. Never retry malformed blobs or privacy guardrails.
                if str(exc) != "private_storage_api_failed" or attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
    except (ExtensionError, StructuredActionError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "source_identifiers_exposed": False}))
        return 1
    except PrivateHistoryError:
        print('{"status":"blocked","reason":"private_extension_storage_unavailable","source_identifiers_exposed":false}')
        return 1
    except Exception:
        print('{"status":"blocked","reason":"private_extension_unavailable","source_identifiers_exposed":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
