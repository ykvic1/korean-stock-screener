"""PRIVATE month-complete OpenDART/KRX discontinuity *candidate* cross-check.

Read immutable official OpenDART list.json receipt pages and validated private
KRX quantized-return anomaly flags; compare ticker + proximity only. No source
prices, receipt or corp IDs are published. No close adjustment, corporate-event
confirmation, historical issuer continuity, PIT or trade signal is authorized.

A month is eligible ONLY when all four DART categories/pages are checkpointed,
and all KRX 5-session buckets covering that month were risk-reviewed. Partial
months and not-yet-reviewed anomalies are NEVER interpreted as negative evidence.
"""
from __future__ import annotations

import base64
from datetime import date
from hashlib import sha256
import json
import os
import re

from v3_dart_historical_event_receipts import (
    CHECKPOINT as DART_CHECKPOINT, MONTHS, CATEGORIES, PRIVATE_REPO,
    _check_cursor as dart_cursor, _decode as receipt_decode,
    _path as receipt_path, _bytes as canonical,
)
from v3_krx_corporate_risk_incremental import (
    CHECKPOINT as RISK_CHECKPOINT, _check_progress as risk_cursor,
    _validate_delta as risk_delta, _delta_path as risk_delta_path,
    BASELINE_COHORTS,
)
from v3_krx_derived_corporate_action_risk import (
    _file_end as baseline_path, _read_existing as baseline_validate,
)
from v3_krx_derived_backfill import (
    CHECKPOINT as SOURCE_CHECKPOINT, _manifest as source_validate,
    history_candidates,
)
from v3_krx_private_github_backfill import PrivateGitStorage


SCHEMA = "v3-private-dart-krx-monthly-ticker-date-candidates-v1"
CHECKPOINT = "derived/quality/dart-krx-monthly-candidates-checkpoint.json"
OUT = "derived/quality/dart-krx-monthly-candidates"
MAX_DELTA_FILES = 200
MAX_PAGES_PER_CATEGORY = 200
WINDOW_CALENDAR_DAYS = 14
DATE8 = re.compile(r"[0-9]{8}\Z")
DATE10 = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


class CrosscheckError(ValueError):
    """Fail-closed static errors: never include issuer, prices or receipts."""


def _read(storage, path):
    body, _ = storage.get_content(path)
    if body is None:
        raise CrosscheckError("missing_private_crosscheck_source")
    return body


def _source_events(storage):
    """Strictly validate the complete risk archive, not a partial cursor tail."""
    dates, digest = history_candidates()
    source_raw = _read(storage, SOURCE_CHECKPOINT)
    risk_raw, _ = storage.get_content(RISK_CHECKPOINT)
    try:
        source = source_validate(source_raw, dates, digest)
        state = risk_cursor(risk_raw, dates, digest, source["next_window_index"])
    except Exception:
        raise CrosscheckError("invalid_krx_source_or_risk_checkpoint") from None
    if state is None:
        return [], dates, None, source_raw, risk_raw
    last = state["next_cohort_index"]
    if last < BASELINE_COHORTS:
        raise CrosscheckError("invalid_risk_review_cursor")
    try:
        baseline = baseline_validate(
            _read(storage, baseline_path(dates, BASELINE_COHORTS)),
            dates[5 * BASELINE_COHORTS-1], BASELINE_COHORTS)
    except Exception:
        raise CrosscheckError("invalid_private_risk_baseline") from None
    if baseline.get("historical_calendar_candidate_hash") != digest:
        raise CrosscheckError("risk_baseline_calendar_mismatch")
    events = [{
        "issuer_market": r["issuer_market"], "first_day": r["period_start"],
        "last_day": r["period_end"], "type": r["kind"],
    } for r in baseline["flagged_issuer_periods"]]
    flags = len(events)
    cursor = BASELINE_COHORTS
    if last > BASELINE_COHORTS:
        try:
            files = storage.transport("GET", "contents/derived/quality/batches")
        except Exception:
            raise CrosscheckError("private_risk_inventory_unavailable") from None
        if (type(files) is not list or len(files) > MAX_DELTA_FILES
            or any(type(x) is not dict or x.get("type") != "file"
                   or type(x.get("path")) is not str
                   for x in files)):
            raise CrosscheckError("invalid_private_risk_inventory")
        enumerated = []
        for entry in files:
            path = entry["path"]
            match = re.fullmatch(
                r"derived/quality/batches/([0-9]{4}-[0-9]{2}-[0-9]{2})_"
                r"([0-9]{4}-[0-9]{2}-[0-9]{2})\.json\.gz", path)
            if not match:
                raise CrosscheckError("invalid_private_risk_file_name")
            try:
                start = dates.index(match.group(1)) // 5
                end = (dates.index(match.group(2)) + 1) // 5
            except ValueError:
                raise CrosscheckError("unmapped_private_risk_interval") from None
            if risk_delta_path(dates, start, end) != path:
                raise CrosscheckError("misaligned_private_risk_interval")
            enumerated.append((start, end, path))
        enumerated.sort()
        for start, end, path in enumerated:
            # An immutable chunk may have been saved before a crashed cursor
            # update; ignore it until independently committed by risk review.
            if start >= last:
                continue
            if start != cursor or not cursor < end <= last:
                raise CrosscheckError("private_risk_inventory_gap_or_overlap")
            chunk = risk_delta(_read(storage, path), days=dates,
                               start=start, end=end)
            events.extend(chunk["flags"])
            flags += chunk["flag_count"]
            cursor = end
        if cursor != last:
            raise CrosscheckError("private_risk_inventory_incomplete")
        if (cursor != last or
            sha256(_read(storage, state["last_audit_file"])).hexdigest()
            != state["last_audit_sha256"]):
            raise CrosscheckError("private_risk_tail_changed")
    elif sha256(_read(storage, state["last_audit_file"])).hexdigest() != state["last_audit_sha256"]:
        raise CrosscheckError("private_risk_baseline_tail_changed")
    if flags != state["cumulative_price_discontinuity_flags"]:
        raise CrosscheckError("private_risk_flag_count_mismatch")
    return events, dates, state, source_raw, risk_raw


def _month_receipts(storage, month):
    """Four categories must each be COMPLETE, including their final page."""
    all_receipts = []
    seen = set()
    for category in CATEGORIES:
        first = receipt_decode(_read(storage, receipt_path(month, category, 1)),
                               month, category, 1)
        pages = first["total_pages"]
        if pages > MAX_PAGES_PER_CATEGORY:
            raise CrosscheckError("dart_month_category_unbounded")
        if pages == 0:
            continue
        count = 0
        for page in range(1, pages+1):
            data = first if page == 1 else receipt_decode(
                _read(storage, receipt_path(month, category, page)),
                month, category, page)
            if data["total_pages"] != pages or data["total_count"] != first["total_count"]:
                raise CrosscheckError("dart_category_pagination_changed")
            for r in data["receipts"]:
                if r["rcept_no"] in seen:
                    raise CrosscheckError("dart_receipt_duplicate_across_pages")
                seen.add(r["rcept_no"])
                all_receipts.append(r)
                count += 1
        if count != first["total_count"]:
            raise CrosscheckError("dart_category_missing_receipts")
    return all_receipts


def _crossmatch(events, receipts, month):
    """Ticker/date RESEARCH candidates only, even if a title looks convincing."""
    # Never assume ticker == historically continuous issuer; the OpenDART list
    # result does not authenticate KRX market or effective corporate action.
    index = {}
    for row in receipts:
        if row["rcept_dt"][:6] != month.replace("-", ""):
            raise CrosscheckError("dart_receipt_outside_complete_month")
        if row["stock_code"] is not None:
            index.setdefault(row["stock_code"], []).append(
                date.fromisoformat(row["rcept_dt"][:4] + "-" +
                                    row["rcept_dt"][4:6] + "-" +
                                    row["rcept_dt"][6:]))
    eligible = [f for f in events if f["last_day"][:7] == month]
    matched = pairs = 0
    for flag in eligible:
        key = flag["issuer_market"].split(":")
        if len(key) != 2 or key[0] not in ("KOSPI", "KOSDAQ"):
            raise CrosscheckError("invalid_krx_anomaly_issuer")
        try:
            end = date.fromisoformat(flag["last_day"])
        except ValueError:
            raise CrosscheckError("invalid_krx_anomaly_day") from None
        candidates = sum(
            abs((stamp - end).days) <= WINDOW_CALENDAR_DAYS
            for stamp in index.get(key[1], ()))
        matched += int(candidates > 0)
        pairs += candidates
    return {
        "risk_events_within_completed_month": len(eligible),
        "ticker_date_nearby_event_count": matched,
        "ticker_date_candidate_pairs": pairs,
        "risk_events_without_same_month_nearby_receipt": len(eligible)-matched,
        "coverage_gaps_not_negative_evidence": True,
        "identity_or_corporate_action_verified": False,
        "split_adjustment_authorized": False,
        "historical_pit_verified": False,
        "trading_score_authorized": False,
    }


def _empty():
    return {"schema": SCHEMA, "next_month_index": 0,
            "last_month_file": None, "last_month_digest": None,
            "corporate_actions_verified": False, "score_authorized": False}


def _checkpoint(raw):
    if raw is None:
        return _empty()
    try:
        obj = json.loads(raw)
        idx = obj.get("next_month_index")
        if (obj.get("schema") != SCHEMA or type(idx) is not int
            or not 0 <= idx <= len(MONTHS)
            or obj.get("corporate_actions_verified") is not False
            or obj.get("score_authorized") is not False):
            raise ValueError()
        if idx == 0:
            if obj.get("last_month_file") is not None or obj.get("last_month_digest") is not None:
                raise ValueError()
        elif (obj.get("last_month_file") != f"{OUT}/{MONTHS[idx-1]}.json"
              or type(obj.get("last_month_digest")) is not str
              or not DIGEST.fullmatch(obj["last_month_digest"])):
            raise ValueError()
        return obj
    except Exception:
        raise CrosscheckError("invalid_private_match_checkpoint") from None


def run(storage):
    if storage.repository != PRIVATE_REPO:
        raise CrosscheckError("public_or_untrusted_private_archive")
    storage.verify_private()
    dartraw, _ = storage.get_content(DART_CHECKPOINT)
    try:
        dart = dart_cursor(dartraw)
    except Exception:
        raise CrosscheckError("invalid_dart_source_receipt_cursor") from None
    state_raw, state_sha = storage.get_content(CHECKPOINT)
    state = _checkpoint(state_raw)
    idx = state["next_month_index"]
    if idx:
        prior = _read(storage, state["last_month_file"])
        if sha256(prior).hexdigest() != state["last_month_digest"]:
            raise CrosscheckError("private_monthly_match_tail_changed")
    if idx >= dart["month_index"]:
        return {"status": "waiting_for_complete_official_filing_month",
                "completed_dart_months": dart["month_index"],
                "crosschecked_months": idx, "provider_calls": 0,
                "confirmed_corporate_actions": 0, "scores_promoted": 0}
    events, days, risk, source_raw, risk_raw = _source_events(storage)
    if risk is None:
        return {"status": "waiting_for_private_risk_review",
                "completed_dart_months": dart["month_index"],
                "crosschecked_months": idx, "provider_calls": 0,
                "confirmed_corporate_actions": 0, "scores_promoted": 0}
    month = MONTHS[idx]
    # Monthly KRX anomaly population cannot be complete if last reviewed
    # cohort ends before the month's final candidate exchange session.
    related = [d for d in days if d[:7] == month]
    if (not related or days[5*risk["next_cohort_index"]-1] < related[-1]):
        return {"status": "waiting_for_full_month_krx_risk_review",
                "completed_dart_months": dart["month_index"],
                "crosschecked_months": idx, "provider_calls": 0,
                "confirmed_corporate_actions": 0, "scores_promoted": 0}
    receipts = _month_receipts(storage, month)
    report = {
        "schema": SCHEMA, "month": month,
        "dart_categories_complete": list(CATEGORIES),
        "private_official_filing_receipts_examined": len(receipts),
        "comparison_window_calendar_days": WINDOW_CALENDAR_DAYS,
        "comparison_scope": "same_month_ticker_date_only_not_event_proof",
        **_crossmatch(events, receipts, month),
    }
    payload = canonical(report)
    path = f"{OUT}/{month}.json"
    # Refuse mixing two concurrently mutating source checkpoints before
    # creating any new private output or advancing this run's cursor.
    if (storage.get_content(DART_CHECKPOINT)[0] != dartraw
        or storage.get_content(RISK_CHECKPOINT)[0] != risk_raw
        or storage.get_content(SOURCE_CHECKPOINT)[0] != source_raw):
        raise CrosscheckError("source_cursors_changed_during_match")
    previous, _ = storage.get_content(path)
    if previous is not None and previous != payload:
        raise CrosscheckError("immutable_private_monthly_crosscheck_changed")
    if previous is None:
        storage.put_new(path, payload, "Save private unverified DART KRX candidate month")
    written = _read(storage, path)
    if written != payload:
        raise CrosscheckError("private_monthly_crosscheck_not_read_back")
    next_state = {
        **state, "next_month_index": idx+1,
        "last_month_file": path,
        "last_month_digest": sha256(payload).hexdigest(),
    }
    newstate = canonical(next_state)
    if state_sha is None:
        storage.put_new(CHECKPOINT, newstate, "Start private DART KRX match progress")
    else:
        storage.transport("PUT", "contents/"+CHECKPOINT, {
            "message": "Advance private DART KRX monthly candidate cursor",
            "content": base64.b64encode(newstate).decode("ascii"),
            "sha": state_sha,
        })
    if _read(storage, CHECKPOINT) != newstate:
        raise CrosscheckError("private_match_checkpoint_not_read_back")
    dart_after, _ = storage.get_content(DART_CHECKPOINT)
    risk_after, _ = storage.get_content(RISK_CHECKPOINT)
    # The source may have progressed concurrently (another workflow). Do not
    # claim a stable comparison if either cursor changed in the meantime.
    # Outputs are only a bounded private research candidate, never an answer.
    if (dart_after != dartraw or risk_after != risk_raw or
        storage.get_content(SOURCE_CHECKPOINT)[0] != source_raw):
        raise CrosscheckError("source_cursors_changed_during_match")
    return {"status": "private_unverified_candidate_month_completed",
            "completed_dart_months": dart["month_index"],
            "crosschecked_months": idx+1,
            "filing_receipts_examined": len(receipts),
            **{k:report[k] for k in
               ("risk_events_within_completed_month",
                "ticker_date_nearby_event_count", "ticker_date_candidate_pairs")},
            "provider_calls": 0, "confirmed_corporate_actions": 0,
            "scores_promoted": 0, "public_issuer_records_written": 0}


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20):
        raise SystemExit("blocked: private-only historical crosscheck credentials missing")
    try:
        store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        result = run(store)
    except Exception:
        raise SystemExit("blocked: private DART KRX candidate review validation failed") from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
