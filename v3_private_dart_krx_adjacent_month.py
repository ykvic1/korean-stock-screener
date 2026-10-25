"""PRIVATE adjacent-month OpenDART/KRX date-proximity research (zero provider calls).

The original same-month checkpoint is immutable. This separate append-only
chain examines the boundary *between* two fully archived filing months, so
late/early filing dates are not silently omitted from the 14-day candidate
window. A stock-code/date coincidence is NEVER proof of issuer continuity,
corporate action, effective date, adjusted price, PIT, or a trading signal.
"""
from __future__ import annotations

import base64
from datetime import date
from hashlib import sha256
import json
import os
import re

from v3_dart_historical_event_receipts import (
    CHECKPOINT as DART_CHECKPOINT, MONTHS, PRIVATE_REPO,
    _check_cursor as dart_cursor, _bytes as canonical,
)
from v3_private_dart_krx_event_crosscheck import (
    WINDOW_CALENDAR_DAYS, _month_receipts, _source_events,
    SOURCE_CHECKPOINT, RISK_CHECKPOINT,
)
from v3_krx_private_github_backfill import PrivateGitStorage


SCHEMA = "v3-private-dart-krx-adjacent-month-date-candidates-v1"
CHECKPOINT = "derived/quality/dart-krx-adjacent-month-candidates-checkpoint.json"
OUT = "derived/quality/dart-krx-adjacent-month-candidates"
SHA = re.compile(r"[0-9a-f]{64}\Z")
STOCK = re.compile(r"[0-9]{6}\Z")


class AdjacentMonthError(ValueError):
    """Never include identifiers, filing bodies or prices in errors."""


# Every diagnostic is a fixed, source-independent label. Raw private
# receipts, tickers, price events, tokens and transport exception text
# must never enter public GitHub Actions logs.
SAFE_ERROR_CODES = frozenset({
    "adjacent_month_source_cursor_changed",
    "invalid_or_incomplete_private_adjacent_month_inputs",
    "invalid_private_adjacent_month_checkpoint",
    "invalid_private_adjacent_month_receipt",
    "invalid_private_adjacent_month_ticker",
    "invalid_private_dart_receipt_source",
    "invalid_private_filing_date",
    "invalid_private_krx_candidate",
    "invalid_private_krx_candidate_day",
    "invalid_private_krx_candidate_market",
    "invalid_private_krx_candidate_ticker",
    "invalid_private_krx_risk_chain",
    "missing_private_adjacent_month_evidence",
    "private_adjacent_month_cursor_readback_failed",
    "private_adjacent_month_immutable_collision",
    "private_adjacent_month_report_readback_failed",
    "private_adjacent_month_tail_changed",
    "untrusted_or_public_adjacent_month_destination",
})


def _read(storage, path):
    body, _ = storage.get_content(path)
    if body is None:
        raise AdjacentMonthError("missing_private_adjacent_month_evidence")
    return body


def _pair_path(i):
    return f"{OUT}/{MONTHS[i]}--{MONTHS[i+1]}.json"


def _empty():
    return {
        "schema": SCHEMA, "next_pair_index": 0,
        "last_pair_file": None, "last_pair_digest": None,
        "corporate_actions_verified": False, "scores_authorized": False,
    }


def _checkpoint(raw):
    if raw is None:
        return _empty()
    try:
        x = json.loads(raw)
        i = x.get("next_pair_index")
        if (x.get("schema") != SCHEMA or type(i) is not int
            or not 0 <= i <= len(MONTHS)-1
            or x.get("corporate_actions_verified") is not False
            or x.get("scores_authorized") is not False):
            raise ValueError()
        if i == 0:
            if x.get("last_pair_file") is not None or x.get("last_pair_digest") is not None:
                raise ValueError()
        elif (x.get("last_pair_file") != _pair_path(i-1)
              or type(x.get("last_pair_digest")) is not str
              or not SHA.fullmatch(x["last_pair_digest"])):
            raise ValueError()
        return x
    except Exception:
        raise AdjacentMonthError("invalid_private_adjacent_month_checkpoint") from None


def _day(stamp):
    try:
        if (type(stamp) is not str or not re.fullmatch(r"[0-9]{8}", stamp)):
            raise ValueError()
        return date.fromisoformat(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}")
    except ValueError:
        raise AdjacentMonthError("invalid_private_filing_date") from None


def _count_boundary_candidates(events, left_receipts, right_receipts, left, right):
    """Only opposite-month filing dates count; same-month matches stay separate."""
    by_month = {}
    for month, rows in ((left, left_receipts), (right, right_receipts)):
        mapping = {}
        for r in rows:
            if (type(r) is not dict or type(r.get("stock_code")) not in (str, type(None))
                or type(r.get("rcept_dt")) is not str
                or not r["rcept_dt"].startswith(month.replace("-", ""))):
                raise AdjacentMonthError("invalid_private_adjacent_month_receipt")
            ticker = r["stock_code"]
            if ticker is not None:
                if not STOCK.fullmatch(ticker):
                    raise AdjacentMonthError("invalid_private_adjacent_month_ticker")
                mapping.setdefault(ticker, []).append(_day(r["rcept_dt"]))
        by_month[month] = mapping

    examined = matching = pairs = 0
    for flag in events:
        if type(flag) is not dict:
            raise AdjacentMonthError("invalid_private_krx_candidate")
        stamp = flag.get("last_day")
        if type(stamp) is not str or stamp[:7] not in (left, right):
            continue
        try:
            end = date.fromisoformat(stamp)
        except ValueError:
            raise AdjacentMonthError("invalid_private_krx_candidate_day") from None
        issuer_market = flag.get("issuer_market")
        if type(issuer_market) is not str:
            raise AdjacentMonthError("invalid_private_krx_candidate_ticker")
        parts = issuer_market.split(":")
        if (len(parts) != 2 or parts[0] not in ("KOSPI", "KOSDAQ")
            or not STOCK.fullmatch(parts[1])):
            raise AdjacentMonthError("invalid_private_krx_candidate_market")
        opposite = right if stamp[:7] == left else left
        eligible = sum(
            abs((filing - end).days) <= WINDOW_CALENDAR_DAYS
            for filing in by_month[opposite].get(parts[1], ())
        )
        examined += 1
        matching += int(eligible > 0)
        pairs += eligible
    return {
        "price_discontinuity_events_examined_in_pair": examined,
        "opposite_month_nearby_event_count": matching,
        "opposite_month_receipt_candidate_pairs": pairs,
        "absence_is_not_negative_corporate_action_evidence": True,
        "ticker_history_identity_verified": False,
        "filing_kind_or_effective_date_verified": False,
        "adjusted_prices_certified": False,
        "historical_pit_verified": False,
        "trading_scores_authorized": False,
    }


def run(storage):
    if storage.repository != PRIVATE_REPO:
        raise AdjacentMonthError("untrusted_or_public_adjacent_month_destination")
    storage.verify_private()
    dartraw, _ = storage.get_content(DART_CHECKPOINT)
    try:
        dart = dart_cursor(dartraw)
    except Exception:
        raise AdjacentMonthError("invalid_private_dart_receipt_source") from None
    original, original_sha = storage.get_content(CHECKPOINT)
    state = _checkpoint(original)
    i = state["next_pair_index"]
    if i:
        previous = _read(storage, state["last_pair_file"])
        if sha256(previous).hexdigest() != state["last_pair_digest"]:
            raise AdjacentMonthError("private_adjacent_month_tail_changed")
    if i >= len(MONTHS)-1 or dart["month_index"] < i+2:
        return {
            "status": "waiting_for_two_complete_adjacent_dart_months",
            "completed_dart_months": dart["month_index"],
            "crosschecked_adjacent_month_pairs": i, "provider_calls": 0,
            "confirmed_corporate_actions": 0, "scores_promoted": 0,
        }

    try:
        events, days, risk, source_raw, risk_raw = _source_events(storage)
    except Exception:
        raise AdjacentMonthError("invalid_private_krx_risk_chain") from None
    if risk is None:
        return {"status": "waiting_for_verified_krx_risk_review",
                "crosschecked_adjacent_month_pairs": i,
                "provider_calls": 0, "confirmed_corporate_actions": 0,
                "scores_promoted": 0}

    left, right = MONTHS[i], MONTHS[i+1]
    related = [d for d in days if d[:7] == right]
    if (not related or
        days[5*risk["next_cohort_index"]-1] < related[-1]):
        return {"status": "waiting_for_full_second_month_krx_risk_review",
                "crosschecked_adjacent_month_pairs": i,
                "provider_calls": 0, "confirmed_corporate_actions": 0,
                "scores_promoted": 0}

    try:
        left_rows = _month_receipts(storage, left)
        right_rows = _month_receipts(storage, right)
        counts = _count_boundary_candidates(events, left_rows, right_rows, left, right)
    except Exception:
        raise AdjacentMonthError("invalid_or_incomplete_private_adjacent_month_inputs") from None

    report = {
        "schema": SCHEMA, "first_month": left, "second_month": right,
        "comparison_window_calendar_days": WINDOW_CALENDAR_DAYS,
        "comparison_scope": "opposite_month_ticker_date_only_not_event_proof",
        "first_month_official_filing_receipts_examined": len(left_rows),
        "second_month_official_filing_receipts_examined": len(right_rows),
        "krx_source_begins_within_this_pair": days[0][:7] in (left, right),
        "krx_entire_calendar_month_history_certified": False,
        "selected_dart_categories_exhaustive": False,
        "public_issuer_data_published": False,
        **counts,
    }
    payload = canonical(report)
    path = _pair_path(i)
    def unchanged_sources():
        return (
            storage.get_content(DART_CHECKPOINT)[0] == dartraw
            and storage.get_content(RISK_CHECKPOINT)[0] == risk_raw
            and storage.get_content(SOURCE_CHECKPOINT)[0] == source_raw
        )
    if not unchanged_sources():
        raise AdjacentMonthError("adjacent_month_source_cursor_changed")
    previous, _ = storage.get_content(path)
    if previous is not None and previous != payload:
        raise AdjacentMonthError("private_adjacent_month_immutable_collision")
    if previous is None:
        storage.put_new(path, payload, "Save private adjacent-month filing date candidates")
    if _read(storage, path) != payload:
        raise AdjacentMonthError("private_adjacent_month_report_readback_failed")
    next_state = {
        **state, "next_pair_index": i+1,
        "last_pair_file": path, "last_pair_digest": sha256(payload).hexdigest(),
    }
    data = canonical(next_state)
    if original_sha is None:
        storage.put_new(CHECKPOINT, data, "Start private adjacent-month candidate cursor")
    else:
        storage.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance private adjacent-month candidate cursor",
            "content": base64.b64encode(data).decode("ascii"),
            "sha": original_sha,
        })
    if _read(storage, CHECKPOINT) != data:
        raise AdjacentMonthError("private_adjacent_month_cursor_readback_failed")
    if not unchanged_sources():
        raise AdjacentMonthError("adjacent_month_source_cursor_changed")
    return {
        "status": "private_unverified_adjacent_month_pair_completed",
        "completed_dart_months": dart["month_index"],
        "crosschecked_adjacent_month_pairs": i+1,
        "opposite_month_nearby_event_count": counts["opposite_month_nearby_event_count"],
        "opposite_month_receipt_candidate_pairs": counts["opposite_month_receipt_candidate_pairs"],
        "provider_calls": 0, "confirmed_corporate_actions": 0,
        "scores_promoted": 0, "public_issuer_records_written": 0,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20):
        raise SystemExit("blocked: private adjacent-month research credentials missing")
    try:
        store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        result = run(store)
    except AdjacentMonthError as exc:
        # Expose only a vetted failure CLASS, never the private source.
        reason = str(exc)
        if reason not in SAFE_ERROR_CODES:
            reason = "unclassified_validation"
        raise SystemExit(
            "blocked: private adjacent-month evidence validation failed (" +
            reason + ")") from None
    except Exception:
        # HTTP, GitHub, provider and arbitrary runtime errors may embed
        # private URLs or payloads, so they remain deliberately generic.
        raise SystemExit("blocked: private adjacent-month evidence validation failed") from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
