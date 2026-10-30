"""Weekly official DART BS/IS freshness audit, never an archive overwrite.

Four independent daily slices inspect one seventh of the listed roster.
The existing public historical archive remains immutable: revised receipts,
missing filings and changed values are QUARANTINED for later verified rebind.
Only reason codes and stock identifiers enter the report; never raw API rows.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import time

import v3_dart_bulk_history as bulk

SCHEMA = "v3-dart-seven-day-official-fiscal-drift-audit-0.1"
EPOCH = date(2026, 10, 20)
DAYS = 7
PARTS_PER_DAY = 4
MAX_FISCAL_CALLS = 16
MAX_ISSUERS_PER_PART = 90
MAX_SOURCE_SECONDS = 1200


class AuditError(ValueError):
    """Safe fixed diagnostic; never print remote response, URL or API key."""


def slice_codes(codes, when, part):
    if type(when) is not date or when < EPOCH or type(part) is not int or not 0 <= part < PARTS_PER_DAY:
        raise AuditError("invalid_weekly_audit_selection")
    names = sorted(codes)
    if not names or len(names) != len(set(names)) or not 1 <= len(names) <= 6000:
        raise AuditError("invalid_weekly_market_universe")
    slot = (when - EPOCH).days % DAYS
    first = sum(divmod(len(names), DAYS)[0] + int(i < len(names) % DAYS) for i in range(slot))
    length = len(names) // DAYS + int(slot < len(names) % DAYS)
    daily = names[first:first + length]
    offset = sum(len(daily) // PARTS_PER_DAY + int(i < len(daily) % PARTS_PER_DAY)
                 for i in range(part))
    part_size = len(daily) // PARTS_PER_DAY + int(part < len(daily) % PARTS_PER_DAY)
    selected = daily[offset:offset + part_size]
    if len(selected) > MAX_ISSUERS_PER_PART:
        raise AuditError("weekly_audit_provider_batch_too_large")
    return slot, selected


def prepare(market, catalog, archive, when, part):
    asof, members = bulk._inputs(market, catalog)
    market_day = bulk._day(asof)
    if when < market_day or (when - market_day).days > 10:
        raise AuditError("stale_or_future_weekly_audit_market_date")
    fiscal = list(range(market_day.year - 4, market_day.year))
    if (type(archive) is not dict or archive.get("schema") != bulk.SCHEMA or
            archive.get("fiscal_years") != fiscal or
            type(archive.get("records")) is not dict):
        raise AuditError("untrusted_annual_history_snapshot")
    baseline_day = bulk._day(archive.get("asof"))
    if baseline_day > market_day or (market_day - baseline_day).days > 10:
        raise AuditError("stale_or_future_annual_history_baseline")
    prior_codes = set(archive["records"])
    current_codes = set(members)
    if archive.get("listed_total") != len(members) or prior_codes != current_codes:
        # The KRX listing census can change before the independent annual
        # collector refreshes its snapshot. Never compare two issuer rosters.
        # A bounded, self-consistent *older* baseline can only WAIT: it must
        # not reach the official API or claim that any source was verified.
        if (baseline_day < market_day
            and type(archive.get("listed_total")) is int
            and archive["listed_total"] == len(prior_codes)
            and archive.get("attempted_company_count") == len(prior_codes)
            and len(prior_codes.symmetric_difference(current_codes)) <= 25
            and all(type(code) is str and bulk.CODE.fullmatch(code)
                    and type(row) is dict and type(row.get("years")) is dict
                    for code, row in archive["records"].items())):
            return {"ready": False, "asof": asof, "baseline_asof": archive["asof"],
                    "members": members, "fiscal": fiscal, "previous": {},
                    "slot": None, "selected": [],
                    "wait_status": "waiting_for_same_roster_annual_history"}
        raise AuditError("untrusted_annual_history_snapshot")
    previous = bulk._validate_prior(archive, asof, members)
    if (archive.get("attempted_company_count") != len(members) or
            len(previous) != len(members) or
            any(any(str(y) not in previous[c]["years"] for y in fiscal) for c in members)):
        return {"ready": False, "asof": asof, "baseline_asof": archive["asof"], "members": members,
                "fiscal": fiscal, "previous": previous, "slot": None, "selected": []}
    slot, selected = slice_codes(members, when, part)
    return {"ready": True, "asof": asof, "baseline_asof": archive["asof"], "members": members, "fiscal": fiscal,
            "previous": previous, "slot": slot, "selected": selected}


def changes(previous, newly_fetched, selected, fiscal, unmapped=()):
    """Compare source-derived annual accounts without emitting any values."""
    missing = set(unmapped)
    results = {"unchanged": 0, "revised": 0, "newly_observed": 0,
               "previously_observed_now_missing": 0, "unmapped": 0}
    anomalies = []
    for code in selected:
        for year in fiscal:
            if code in missing:
                kind = "unmapped"
            else:
                before = previous[code]["years"][str(year)]
                after = newly_fetched[code][str(year)]
                if before == after:
                    kind = "unchanged"
                elif not before and after:
                    kind = "newly_observed"
                elif before and not after:
                    kind = "previously_observed_now_missing"
                else:
                    kind = "revised"
            results[kind] += 1
            if kind != "unchanged":
                anomalies.append({"code": code, "fiscal_year": year, "reason": kind})
    return results, anomalies


def run(market, catalog, archive, *, when, part, mode, key=None, session=None):
    if mode not in ("audit", "live"):
        raise AuditError("unsupported_weekly_audit_mode")
    state = prepare(market, catalog, archive, when, part)
    base = {
        "schema": SCHEMA, "run_kst": when.isoformat(),
        "market_asof": state["asof"], "financial_baseline_asof": state["baseline_asof"],
        "cohort_index": state["slot"],
        "part_index": part, "listed_total": len(state["members"]),
        "selected_company_count": len(state["selected"]),
        "fiscal_years": state["fiscal"],
        "source_api_calls": 0, "source_archive_edited": False,
        "historical_PIT_certified": False, "buy_signals_authorized": False,
        "raw_official_data_saved": False, "full_v3_ready": False,
        "comparison_counts": None, "drift_flags": [],
        "source_roster_sha256": sha256(
            json.dumps(sorted(state["members"]), separators=(",", ":")).encode("ascii")
        ).hexdigest(),
    }
    if not state["ready"]:
        return {**base, "status": state.get(
            "wait_status", "waiting_for_first_complete_dart_backfill")}
    if mode == "audit":
        return {**base, "status": "bounded_rotation_plan_only"}
    if not isinstance(key, str) or len(key) < 20 or session is None:
        raise AuditError("weekly_audit_provider_not_configured")
    selected = state["selected"]
    if not selected:
        raise AuditError("empty_weekly_audit_slice")
    try:
        response = session.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": key}, timeout=(20, 95)
        )
        response.raise_for_status()
        mapping = bulk._mapping(response.content, set(selected))
    except Exception:
        raise AuditError("weekly_audit_corporation_mapping_unavailable") from None
    try:
        # Force REFRESH in memory only: never pass previously saved year keys.
        observed, calls = bulk.fetch_group(
            session, key, when.isoformat(), state["fiscal"], selected, mapping,
            prior_years={code: {} for code in selected},
            max_requests=MAX_FISCAL_CALLS,
            deadline=time.monotonic() + MAX_SOURCE_SECONDS
        )
    except bulk.BulkHistoryError:
        raise AuditError("weekly_audit_dart_source_or_quota_stop") from None
    if not 0 <= calls <= MAX_FISCAL_CALLS:
        raise AuditError("weekly_audit_source_budget_exceeded")
    counts, flags = changes(state["previous"], observed, selected, state["fiscal"],
                            unmapped=set(selected) - set(mapping))
    return {**base, "source_api_calls": 1 + calls,
            "comparison_counts": counts, "drift_flags": flags,
            "status": "source_drift_quarantined" if flags else "same_source_confirmed"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("audit", "live"), default="audit")
    p.add_argument("--run-day", required=True, help="Asia/Seoul YYYY-MM-DD")
    p.add_argument("--part", type=int, choices=range(PARTS_PER_DAY), required=True)
    p.add_argument("--market", default="docs/data.json")
    p.add_argument("--catalog", default="docs/v22-market-catalog.json")
    p.add_argument("--history", default="docs/v3-dart-bulk-history.json")
    p.add_argument("--output", default="reports/v3-dart-weekly-source-audit.json")
    a = p.parse_args(argv)
    when = bulk._day(a.run_day)
    if when < EPOCH:
        raise AuditError("weekly_audit_date_before_start")
    try:
        market, catalog, archive = [
            json.loads(Path(path).read_text(encoding="utf8"))
            for path in (a.market, a.catalog, a.history)
        ]
    except (OSError, ValueError):
        raise AuditError("weekly_audit_input_unavailable") from None
    if a.mode == "live":
        import os
        import requests
        secret = os.environ.get("DART_API_KEY")
        report = run(market, catalog, archive, when=when, part=a.part,
                     mode="live", key=secret, session=requests.Session())
    else:
        report = run(market, catalog, archive, when=when, part=a.part,
                     mode="audit")
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n", encoding="utf8")
    print(json.dumps({k: report[k] for k in (
        "status", "run_kst", "cohort_index", "part_index",
        "selected_company_count", "source_api_calls",
        "source_archive_edited", "comparison_counts"
    )}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
