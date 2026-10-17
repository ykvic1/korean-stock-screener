"""Read-only, source-pinned v3 *triage* for the original 53 OHLCV failures.

Uses existing published v2.2 measurements. Does not download prices, fix
corporate actions, fill missing days, certify KRX sessions, or make scores.
A contiguous numeric suffix is never a repaired full historical series.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

from baseline_cohort import load_cohort

MIN_NUMERIC_ROWS = 650
BASELINE_SIZE = 53
MAX_REPORT_BYTES = 16_000_000
KINDS = ("quarantined_suffix_short", "raw_observed_history_short",
         "numeric_suffix_only", "other_unverified")


class InvalidFixedCohortEvidence(ValueError):
    pass


def _count(value, label):
    if type(value) is not int or value < 0:
        raise InvalidFixedCohortEvidence("Invalid nonnegative integer: " + label)
    return value


def _date(value, label):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise InvalidFixedCohortEvidence("Invalid evidence date: " + label) from None


def triage_fixed_53(reliability, pinned):
    """Strict 53-symbol identity audit; only input-relative descriptive data.

    Callers MUST load pinned through baseline_cohort.load_cohort() for bytewise
    Git blob verification; arbitrary argument values aren't provenance proof.
    """
    if not isinstance(reliability, dict) or not isinstance(pinned, list):
        raise InvalidFixedCohortEvidence("Invalid research inputs")
    asof = _date(reliability.get("asof"), "asof")
    pins = {p.get("code"): p for p in pinned if isinstance(p, dict)}
    if (len(pinned) != BASELINE_SIZE or len(pins) != BASELINE_SIZE
            or any(not isinstance(k, str) or len(k) != 6 or not k.isdigit()
                   for k in pins)):
        raise InvalidFixedCohortEvidence("Historical pinned 53 absent or corrupt")
    base = reliability.get("baseline_cohort")
    if not isinstance(base, dict) or base.get("requested") != BASELINE_SIZE:
        raise InvalidFixedCohortEvidence("Published fixed-cohort identity absent")
    if base.get("baseline_date") != "2026-10-14":
        raise InvalidFixedCohortEvidence("Original baseline date mismatch")
    records = base.get("stocks")
    if not isinstance(records, list) or len(records) != BASELINE_SIZE:
        raise InvalidFixedCohortEvidence("Fixed-cohort record count mismatch")
    seen = set()
    ordered = []
    for item in records:
        if not isinstance(item, dict):
            raise InvalidFixedCohortEvidence("Invalid stock evidence row")
        code = item.get("code")
        if code not in pins or code in seen or item.get("name") != pins[code]["name"]:
            raise InvalidFixedCohortEvidence("Missing, duplicate or renamed original symbol")
        seen.add(code)
        raw = _count(item.get("rows"), "rows")
        usable = _count(item.get("usable_segment_rows"), "usable_segment_rows")
        quarantined = _count(item.get("quarantined_rows"), "quarantined_rows")
        if raw != usable + quarantined:
            raise InvalidFixedCohortEvidence("Segment partition inconsistent")
        strict = item.get("strict_valid")
        if type(strict) is not bool or strict != (usable >= MIN_NUMERIC_ROWS):
            raise InvalidFixedCohortEvidence("Strict numeric minimum contradicts usable rows")
        if item.get("end") != asof:
            raise InvalidFixedCohortEvidence("Stale or shifted stock snapshot")
        if raw < MIN_NUMERIC_ROWS:
            kind = "raw_observed_history_short"
            if quarantined:
                raise InvalidFixedCohortEvidence("Too-short raw history unexpectedly quarantined")
        elif usable < MIN_NUMERIC_ROWS and quarantined > 0:
            kind = "quarantined_suffix_short"
        elif strict:
            kind = "numeric_suffix_only"
        else:
            kind = "other_unverified"
        cv = item.get("cross_validation")
        if not isinstance(cv, dict):
            raise InvalidFixedCohortEvidence("Independent-source outcome absent")
        state = cv.get("status")
        if state not in ("matched_recent", "mismatch_or_insufficient",
                         "fetch_failed", "unconfirmed"):
            raise InvalidFixedCohortEvidence("Unknown supplier comparison state")
        compared = cv.get("compared_sessions")
        prices = cv.get("matched_sessions")
        volume = cv.get("recent_volume_equal_sessions")
        if compared is not None:
            _count(compared, "compared_sessions")
            _count(prices, "matched_sessions")
            _count(volume, "recent_volume_equal_sessions")
            if prices > compared or volume > compared:
                raise InvalidFixedCohortEvidence("Price/volume counts exceed comparison window")
        elif prices is not None or volume is not None:
            raise InvalidFixedCohortEvidence("Unbounded matching count")

        # This is NOT official source certification. NAVER and Yahoo daily
        # volumes can differ by session, and source series may be adjusted.
        price_disagreements = compared - prices if compared is not None else None
        volume_disagreements = compared - volume if compared is not None else None
        observed = {
            "code": code, "name": pins[code]["name"], "market": pins[code]["market"],
            "raw_observations": raw, "usable_numeric_suffix": usable,
            "quarantined_prefix": quarantined, "last_bad_date": item.get("last_bad_date"),
            "numeric_suffix_usable": strict, "triage_kind": kind,
            "naver_yahoo_comparison_status": state,
            "compared_sessions": compared,
            "price_disagreement_sessions": price_disagreements,
            "volume_disagreement_sessions": volume_disagreements,
            "official_krx_regular_price_volume_verified": False,
            "full_history_verified": False,
            "scoring_allowed": False,
            "next_step": (
                "Review invalid raw rows and corporate-action/suspension evidence; do not splice"
                if kind == "quarantined_suffix_short" else
                "Verify original listing/history coverage; never fabricate pre-listing days"
                if kind == "raw_observed_history_short" else
                "Reconcile full history, actions, trading calendar and session scope"
            ),
        }
        ordered.append(observed)
    if seen != set(pins):
        raise InvalidFixedCohortEvidence("Pinned historical universe mismatch")
    expected_usable = sum(x["numeric_suffix_usable"] for x in ordered)
    if base.get("numeric_segment_usable") != expected_usable:
        raise InvalidFixedCohortEvidence("Published numeric suffix count disagrees")
    if base.get("fetched") != BASELINE_SIZE:
        raise InvalidFixedCohortEvidence("The 53 raw responses were not all observed")
    counts = Counter(r["triage_kind"] for r in ordered)
    # Recoverable here means 'candidate requiring further investigation';
    # NOT numeric repair or verified history.
    priority = {
        "quarantined_suffix_short": 0,
        "raw_observed_history_short": 1,
        "numeric_suffix_only": 2,
        "other_unverified": 3,
    }
    ordered.sort(key=lambda x: (priority[x["triage_kind"]],
                                x["usable_numeric_suffix"], x["code"]))
    return {
        "schema": "v3-original53-offline-triage-0.1",
        "asof": asof,
        "baseline_asof": "2026-10-14",
        "phase": "research_only",
        "requested": BASELINE_SIZE,
        "classification": {name: counts[name] for name in KINDS},
        "needs_raw_error_review": counts["quarantined_suffix_short"],
        "needs_listing_or_history_coverage_review": counts["raw_observed_history_short"],
        "numeric_suffix_usable_unverified": counts["numeric_suffix_only"],
        "independent_recent_price_verified": 0,
        "independent_full_history_verified": 0,
        "regular_session_only_volume_verified": 0,
        "v3_release_ready": False,
        "confirmed_scoring_allowed": False,
        "confirmed_score": None,
        "rows": ordered,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only fixed-53 v3 evidence triage")
    parser.add_argument("--reliability", default="docs/reliability.json")
    args = parser.parse_args(argv)
    path = Path(args.reliability)
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        parser.error("Missing or oversized reliability report")
    try:
        rows = load_cohort()
        report = triage_fixed_53(json.loads(path.read_text(encoding="utf-8")), rows)
    except (ValueError, KeyError, TypeError, OSError, UnicodeError):
        parser.error("Fixed-53 evidence cannot be verified; no recovery certified")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
