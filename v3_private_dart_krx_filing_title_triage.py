"""PRIVATE OpenDART report-title shortlist for KRX discontinuity investigation.

This is a *filing-title hint*, not disclosure-body authentication, issuer
continuity, a real stock action, an effective date, an adjusted return, or PIT.
Requires two COMPLETED DART months, two committed date-match checkpoints, and
a fully reviewed KRX second month. A shortlist may omit real actions because
only selected DART categories and a 14-day observation window are inspected.
All issuer identifiers remain ONLY in the existing verified private repository.
"""
from __future__ import annotations

import base64
from datetime import date
import gzip
from hashlib import sha256
import json
import os
import re

from v3_dart_historical_event_receipts import (
    CHECKPOINT as DART_CHECKPOINT, MONTHS, PRIVATE_REPO,
    _bytes as canonical, _check_cursor as dart_cursor,
)
from v3_private_dart_krx_event_crosscheck import (
    CHECKPOINT as MONTHLY_CHECKPOINT, _checkpoint as monthly_cursor,
    _month_receipts, _source_events, RISK_CHECKPOINT, SOURCE_CHECKPOINT,
    WINDOW_CALENDAR_DAYS,
)
from v3_private_dart_krx_adjacent_month import (
    CHECKPOINT as ADJACENT_CHECKPOINT, _checkpoint as adjacent_cursor,
)
from v3_krx_private_github_backfill import PrivateGitStorage


SCHEMA = "v3-private-dart-krx-title-shortlist-v1"
CHECKPOINT = "derived/quality/dart-krx-title-shortlist-checkpoint.json"
OUT = "derived/quality/dart-krx-title-shortlists"
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
ISSUER = re.compile(r"(?:KOSPI|KOSDAQ):[0-9]{6}\Z")
RECEIPT = re.compile(r"[0-9]{14}\Z")
CORP = re.compile(r"[0-9]{8}\Z")
STOCK = re.compile(r"[0-9]{6}\Z")
MAX_PAIRS = 4000
MAX_BLOB_BYTES = 950_000
HINTS = (
    ("stock_split_consolidation", ("주식분할", "주식병합", "액면분할",
                                   "액면병합", "주식의병합")),
    ("merger_split", ("회사합병", "회사분할", "분할합병", "합병결정", "분할결정")),
    ("capital_reduction", ("감자결정", "자본감소", "자본금감소")),
    ("bonus_issue", ("무상증자",)),
    ("rights_issue", ("유상증자",)),
    ("share_exchange", ("주식교환", "주식의포괄적교환")),
    ("stock_dividend", ("주식배당",)),
)


class TitleTriageError(ValueError):
    """Static errors only; no issuer, price, filing text or receipt IDs."""


def _path(i):
    return f"{OUT}/{MONTHS[i]}--{MONTHS[i+1]}.json.gz"


def _empty():
    return {
        "schema": SCHEMA, "next_pair_index": 0,
        "last_file": None, "last_digest": None,
        "filing_titles_prove_events": False, "scores_authorized": False,
    }


def _cursor(raw):
    if raw is None:
        return _empty()
    try:
        x = json.loads(raw)
        i = x.get("next_pair_index")
        if (type(x) is not dict or x.get("schema") != SCHEMA
            or type(i) is not int or not 0 <= i <= len(MONTHS)-1
            or x.get("filing_titles_prove_events") is not False
            or x.get("scores_authorized") is not False):
            raise ValueError()
        if i == 0:
            if x.get("last_file") is not None or x.get("last_digest") is not None:
                raise ValueError()
        elif (x.get("last_file") != _path(i-1)
              or type(x.get("last_digest")) is not str
              or not DIGEST.fullmatch(x["last_digest"])):
            raise ValueError()
        return x
    except Exception:
        raise TitleTriageError("invalid_private_title_shortlist_cursor") from None


def _day(s):
    try:
        if type(s) is not str:
            raise ValueError()
        if re.fullmatch(r"[0-9]{8}", s):
            value = date.fromisoformat(s[:4] + "-" + s[4:6] + "-" + s[6:])
            if value.strftime("%Y%m%d") != s:
                raise ValueError()
            return value
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
            value = date.fromisoformat(s)
            if value.isoformat() != s:
                raise ValueError()
            return value
        raise ValueError()
    except ValueError:
        raise TitleTriageError("invalid_private_title_candidate_day") from None


def _hint(title):
    if type(title) is not str or not 0 < len(title) <= 500:
        raise TitleTriageError("invalid_private_filing_title")
    normalized = re.sub(r"\s+", "", title)
    matches = [name for name, words in HINTS if any(w in normalized for w in words)]
    # Keywords can appear in a correction, cancellation or unrelated filing.
    # Multiple hints remain unverified and cannot settle event type.
    return matches


def _shortlist(events, receipts, left, right):
    """Bounded ticker+date matches with title hints, for private inspection ONLY."""
    index = {}
    for r in receipts:
        if (type(r) is not dict or type(r.get("rcept_no")) is not str
            or not RECEIPT.fullmatch(r["rcept_no"])
            or type(r.get("corp_code")) is not str or not CORP.fullmatch(r["corp_code"])
            or r.get("stock_code") is not None and (
                type(r["stock_code"]) is not str or not STOCK.fullmatch(r["stock_code"]))):
            raise TitleTriageError("invalid_private_title_source_record")
        filing = _day(r.get("rcept_dt"))
        if filing.strftime("%Y-%m") not in (left, right):
            raise TitleTriageError("receipt_outside_complete_private_pair")
        hint = _hint(r.get("report_nm"))
        if r["stock_code"] is not None:
            index.setdefault(r["stock_code"], []).append((r, filing, hint))

    found = {}
    examined = 0
    for flag in events:
        if type(flag) is not dict:
            raise TitleTriageError("invalid_private_risk_event")
        end = _day(flag.get("last_day"))
        if end.strftime("%Y-%m") not in (left, right):
            continue
        start = _day(flag.get("first_day"))
        code = flag.get("issuer_market")
        kind = flag.get("type")
        if (start > end or type(code) is not str or not ISSUER.fullmatch(code)
            or kind not in ("four_interval_change", "cohort_boundary_change")):
            raise TitleTriageError("invalid_private_title_risk_flag")
        examined += 1
        for receipt, stamp, hints in index.get(code.split(":")[1], ()):
            delta = (stamp - end).days
            if abs(delta) > WINDOW_CALENDAR_DAYS:
                continue
            key = (code, start.isoformat(), end.isoformat(),
                   kind, receipt["rcept_no"])
            record = {
                "heuristic_issuer_market": code,
                "heuristic_first_day": start.isoformat(),
                "heuristic_last_day": end.isoformat(),
                "heuristic_kind": kind,
                "filing_receipt_no": receipt["rcept_no"],
                "filing_corp_code": receipt["corp_code"],
                "filing_stock_code": receipt["stock_code"],
                "filing_date": stamp.isoformat(),
                "days_from_heuristic_end": delta,
                "opposite_calendar_month": stamp.strftime("%Y-%m") != end.strftime("%Y-%m"),
                "report_title_sha256": sha256(receipt["report_nm"].encode("utf-8")).hexdigest(),
                "title_keyword_hints": hints,
                "event_authentication": "not_verified",
            }
            if key in found and found[key] != record:
                raise TitleTriageError("duplicate_candidate_evidence_conflict")
            found[key] = record
            if len(found) > MAX_PAIRS:
                raise TitleTriageError("private_title_shortlist_size_cap")
    ordered = sorted(found.values(), key=lambda r: (
        not bool(r["title_keyword_hints"]),
        abs(r["days_from_heuristic_end"]),
        r["heuristic_last_day"], r["heuristic_issuer_market"],
        r["filing_receipt_no"]))
    return examined, ordered


def run(storage):
    if storage.repository != PRIVATE_REPO:
        raise TitleTriageError("public_or_untrusted_title_shortlist_destination")
    storage.verify_private()
    d_raw, _ = storage.get_content(DART_CHECKPOINT)
    m_raw, _ = storage.get_content(MONTHLY_CHECKPOINT)
    a_raw, _ = storage.get_content(ADJACENT_CHECKPOINT)
    original, original_sha = storage.get_content(CHECKPOINT)
    try:
        d, m, a, state = (dart_cursor(d_raw), monthly_cursor(m_raw),
                          adjacent_cursor(a_raw), _cursor(original))
    except Exception:
        raise TitleTriageError("invalid_upstream_private_title_shortlist_cursor") from None
    i = state["next_pair_index"]
    if i:
        prior, _ = storage.get_content(state["last_file"])
        if prior is None or sha256(prior).hexdigest() != state["last_digest"]:
            raise TitleTriageError("private_title_shortlist_tail_changed")
    if (i >= len(MONTHS)-1 or d["month_index"] < i+2
        or m["next_month_index"] < i+2 or a["next_pair_index"] < i+1):
        return {"status": "waiting_for_two_approved_complete_months",
                "title_shortlist_pairs": i, "provider_calls": 0,
                "confirmed_corporate_actions": 0, "scores_promoted": 0}

    try:
        events, days, risk, source_raw, risk_raw = _source_events(storage)
    except Exception:
        raise TitleTriageError("invalid_private_krx_title_triage_chain") from None
    if risk is None:
        return {"status": "waiting_for_verified_krx_risk_chain",
                "title_shortlist_pairs": i, "provider_calls": 0,
                "confirmed_corporate_actions": 0, "scores_promoted": 0}
    left, right = MONTHS[i], MONTHS[i+1]
    related = [x for x in days if x[:7] == right]
    if not related or days[5*risk["next_cohort_index"]-1] < related[-1]:
        return {"status": "waiting_for_complete_second_month_krx_review",
                "title_shortlist_pairs": i, "provider_calls": 0,
                "confirmed_corporate_actions": 0, "scores_promoted": 0}
    try:
        receipts = _month_receipts(storage, left) + _month_receipts(storage, right)
        examined, matches = _shortlist(events, receipts, left, right)
    except Exception:
        raise TitleTriageError("invalid_private_shortlist_inputs") from None

    payload = canonical({
        "schema": SCHEMA, "first_month": left, "second_month": right,
        "event_source": "private_krx_nonadjusted_price_discontinuity_heuristics",
        "filing_source": "private_complete_selected_OpenDART_list_json_categories",
        "filings_examined": len(receipts),
        "heuristic_events_examined": examined,
        "ticker_date_receipt_pairs": len(matches),
        "title_keyword_pairs": sum(bool(x["title_keyword_hints"]) for x in matches),
        "other_title_pairs": sum(not x["title_keyword_hints"] for x in matches),
        "comparison_window_calendar_days": WINDOW_CALENDAR_DAYS,
        "candidate_rank_rule": "title_keyword_then_absolute_date_distance_only",
        "candidates": matches,
        "filing_title_or_candidate_is_event_proof": False,
        "actual_effective_date_verified": False,
        "historical_issuer_identity_verified": False,
        "selected_dart_filing_categories_exhaustive": False,
        "adjusted_prices_or_pit_returns_certified": False,
        "scores_authorized": False,
        "public_issuer_records_published": False,
    })
    packed = gzip.compress(payload, mtime=0)
    if len(packed) > MAX_BLOB_BYTES:
        raise TitleTriageError("private_title_shortlist_payload_cap")

    tracked = {
        DART_CHECKPOINT: d_raw, MONTHLY_CHECKPOINT: m_raw,
        ADJACENT_CHECKPOINT: a_raw, SOURCE_CHECKPOINT: source_raw,
        RISK_CHECKPOINT: risk_raw,
    }
    def unchanged():
        return all(storage.get_content(path)[0] == data for path, data in tracked.items())
    if not unchanged():
        raise TitleTriageError("private_title_shortlist_sources_changed")
    path = _path(i)
    existing, _ = storage.get_content(path)
    if existing is not None and existing != packed:
        raise TitleTriageError("private_title_shortlist_immutable_collision")
    if existing is None:
        storage.put_new(path, packed, "Save private filing title research shortlist")
    saved, _ = storage.get_content(path)
    if saved != packed or not unchanged():
        raise TitleTriageError("private_title_shortlist_readback_or_source_mismatch")

    new_state = canonical({**state, "next_pair_index": i+1,
                           "last_file": path, "last_digest": sha256(packed).hexdigest()})
    if original_sha is None:
        storage.put_new(CHECKPOINT, new_state, "Start private filing title shortlist cursor")
    else:
        storage.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance private filing title research cursor",
            "content": base64.b64encode(new_state).decode("ascii"),
            "sha": original_sha,
        })
    state_after, _ = storage.get_content(CHECKPOINT)
    if state_after != new_state or not unchanged():
        raise TitleTriageError("private_title_shortlist_cursor_readback_failed")
    return {
        "status": "private_title_keyword_shortlist_completed",
        "title_shortlist_pairs": i+1,
        "ticker_date_receipt_pairs": len(matches),
        "title_keyword_pairs": sum(bool(x["title_keyword_hints"]) for x in matches),
        "other_title_pairs": sum(not x["title_keyword_hints"] for x in matches),
        "provider_calls": 0, "confirmed_corporate_actions": 0,
        "scores_promoted": 0, "public_issuer_records_written": 0,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20):
        raise SystemExit("blocked: private title shortlist credentials unavailable")
    try:
        store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        summary = run(store)
    except Exception:
        raise SystemExit("blocked: private filing title shortlist validation failed") from None
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
