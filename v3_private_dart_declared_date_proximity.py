"""PRIVATE/OFFLINE date-proximity audit: declared DART dates vs KRX risk flags.

Inputs must come from the existing immutable title-shortlist queue and
exact-receipt structured reports. No provider API, private repository write,
publication, corporate-action completion certificate or scoring is performed.

Temporal proximity is *only* a research lead. Planned dates may change and a
large unadjusted KRX price move may be unrelated to the filing.
"""
from __future__ import annotations

from datetime import date
import gzip
from io import BytesIO
import json
import re

from v3_dart_historical_event_receipts import MONTHS
from v3_private_dart_krx_filing_title_triage import WINDOW_CALENDAR_DAYS
from v3_private_dart_original_evidence import _candidate_queue
from v3_private_dart_structured_action_receipts import _validate_report as principal_report
from v3_private_dart_structured_action_extension import _validate_report as supplement_report

SCHEMA = "v3-private-dart-announced-date-krx-risk-proximity-v1"
MAX_SOURCE_BYTES = 2_000_000
MAX_ROWS = 4_000
MAX_BATCH = 8
ISSUER = re.compile(r"(?:KOSPI|KOSDAQ):[0-9A-Z]{6}\Z")


class ProximityError(ValueError):
    """Fixed redacted codes only: never put receipt/issuer/date in errors."""


def _day(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ProximityError("invalid_private_proximity_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ProximityError("invalid_private_proximity_date") from None
    if parsed.isoformat() != value:
        raise ProximityError("invalid_private_proximity_date")
    return parsed


def _windows(source, pair_index, eligible):
    """Read bounded complete private shortlist; never disclose its members."""
    try:
        if type(source) is not bytes or not 0 < len(source) <= MAX_SOURCE_BYTES:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(source), mode="rb") as stream:
            decoded = stream.read(MAX_SOURCE_BYTES + 1)
        if len(decoded) > MAX_SOURCE_BYTES:
            raise ValueError()
        payload = json.loads(decoded)
        rows = payload["candidates"]
        if type(rows) is not list or len(rows) > MAX_ROWS:
            raise ValueError()
    except Exception:
        raise ProximityError("invalid_private_proximity_shortlist") from None
    window_map = {key: set() for key in eligible}
    dedup = set()
    left, right = MONTHS[pair_index:pair_index+2]
    for row in rows:
        try:
            if type(row) is not dict:
                raise ValueError()
            key = (row["filing_receipt_no"], row["filing_corp_code"])
            if key not in window_map:
                # Other valid receipts can coexist in a bounded source queue;
                # this run only audits the selected deterministic slice.
                continue
            issuer = row["heuristic_issuer_market"]
            if type(issuer) is not str or not ISSUER.fullmatch(issuer):
                raise ValueError()
            if issuer.split(":", 1)[1] != row["filing_stock_code"]:
                raise ValueError()
            begin, end, filing = (_day(row["heuristic_first_day"]),
                                  _day(row["heuristic_last_day"]),
                                  _day(row["filing_date"]))
            if begin > end or end.strftime("%Y-%m") not in (left, right):
                raise ValueError()
            if filing.strftime("%Y-%m") not in (left, right):
                raise ValueError()
            if abs((filing - end).days) > WINDOW_CALENDAR_DAYS:
                raise ValueError()
            signature = (key, issuer, begin, end, row["heuristic_kind"])
            if signature in dedup:
                raise ValueError()
            dedup.add(signature)
            window_map[key].add(end)
        except ProximityError:
            raise
        except Exception:
            raise ProximityError("invalid_private_proximity_source_row") from None
    return window_map


def reconcile(shortlist_blob, principal_blobs, supplement_blobs, *,
              pair_index, start=0, limit=MAX_BATCH):
    """Aggregate only a bounded exact-receipt slice with no false event claims.

    Missing private reports are *unexamined*, not negative evidence. The
    independent KRX-derived anomaly is still just a heuristic observation.
    """
    if (type(pair_index) is not int or not 0 <= pair_index < len(MONTHS)-1
        or type(start) is not int or start < 0
        or type(limit) is not int or not 1 <= limit <= MAX_BATCH):
        raise ProximityError("invalid_private_proximity_scope")
    try:
        queue, _, _ = _candidate_queue(shortlist_blob, pair_index)
    except Exception:
        raise ProximityError("invalid_private_proximity_queue") from None
    if start > len(queue):
        raise ProximityError("invalid_private_proximity_offset")
    selected = queue[start:start+limit]
    eligible = {(r["rcept_no"], r["corp_code"]): r for r in selected}
    windows = _windows(shortlist_blob, pair_index, eligible)
    if any(not windows[key] for key in eligible):
        raise ProximityError("private_proximity_unmatched_shortlist_receipt")
    if type(principal_blobs) is not dict or type(supplement_blobs) is not dict:
        raise ProximityError("invalid_private_proximity_report_collection")
    selection = {r["rcept_no"]: r for r in selected}
    if any(type(k) is not str or k not in selection
           for records in (principal_blobs, supplement_blobs)
           for k in records):
        raise ProximityError("private_proximity_report_outside_selected_scope")

    verified = matched = dates = near = distant = 0
    for blobs, validator in ((principal_blobs, principal_report),
                             (supplement_blobs, supplement_report)):
        for receipt, raw in blobs.items():
            rec = selection[receipt]
            try:
                report = validator(raw, pair_index, rec)
            except Exception:
                raise ProximityError("invalid_private_proximity_structured_report") from None
            verified += 1
            matched += len(report["exact_receipt_event_categories"])
            ends = windows[(receipt, rec["corp_code"])]
            for result in report["endpoint_results"]:
                for candidate in result.get("reported_schedule_date_candidates", []):
                    planned = _day(candidate["reported_date"])
                    dates += 1
                    if any(abs((planned - flagged).days) <= WINDOW_CALENDAR_DAYS
                           for flagged in ends):
                        near += 1
                    else:
                        distant += 1
    if near + distant != dates:
        raise ProximityError("invalid_private_proximity_accounting")
    return {
        "schema": SCHEMA,
        "source_pair": MONTHS[pair_index] + "--" + MONTHS[pair_index + 1],
        "selected_unique_receipts": len(selected),
        "distinct_krx_heuristic_end_days": sum(len(v) for v in windows.values()),
        "verified_private_structured_reports": verified,
        "not_yet_saved_report_slots": 2 * len(selected) - verified,
        "exact_receipt_category_matches": matched,
        "declared_schedule_date_fields": dates,
        "declared_dates_near_krx_risk_window": near,
        "declared_dates_outside_krx_risk_window": distant,
        "zero_provider_calls": True,
        "temporal_match_is_corporate_action_proof": False,
        "date_nonmatch_is_absence_of_action_proof": False,
        "official_actual_completion_verified": False,
        "adjusted_prices_certified": False,
        "historical_pit_issuers_certified": False,
        "scores_promoted": 0,
        "private_identity_or_dates_published": False,
    }
