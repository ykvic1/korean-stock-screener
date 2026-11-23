"""Second bounded KRX pre-first-touch LOW batch; reuse all prior official proof.

This module does not run automatically. Explicitly invoking --probe checks only
the five still-unverified, already-ranked dates and refuses to re-query after
the evidence is frozen. Without --probe it makes ZERO KRX requests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from official_probe.clients import KRXClient
from v3_historical_market_coverage import ROOT, day, read_panels, require
from v3_historical_pretouch_official_low_gap import (
    PROBE_SCHEMA, BATCH_LIMIT, SOURCE_DIGESTS,
    saved_official_ohlc_dates, verified_frozen_coverage, prepare,
    check_bounded_batch,
)

SECOND_FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-second-20261123.json"
SCHEMA = "v3-historical-pre-first-low-official-second-frozen-v1"
EXPECTED_DATES = (
    "2025-11-28", "2025-12-02", "2025-12-03",
    "2025-12-04", "2025-12-05",
)
EXPECTED_HOLDER_DAYS = 35


def second_batch_plan(public_panels):
    """No provider calls. Authenticates the first 47 before choosing new dates."""
    previous = verified_frozen_coverage(public_panels, saved_official_ohlc_dates())
    result = previous["report"]
    require(
        result["pre_touch_holder_days_on_existing_official_ohlc_dates"] == 215
        and result["pre_touch_holder_days_without_existing_official_ohlc_date"] == 537
        and result["distinct_pre_touch_official_low_missing_dates"] == 189
        and tuple(result["bounded_next_official_request_dates"]) == EXPECTED_DATES
        and result["bounded_next_issuer_low_observations"] == EXPECTED_HOLDER_DAYS
        and len(previous["batch"]) == BATCH_LIMIT,
        "second_pre_first_low_scope_drift")
    return previous


def probe_second(public_panels, client):
    require(not SECOND_FROZEN.exists(), "already_authenticated_second_pre_first_low_batch")
    prepared = second_batch_plan(public_panels)
    result = check_bounded_batch(public_panels, prepared, client)
    require(tuple(result["scope_dates"]) == EXPECTED_DATES
            and result["official_low_holder_days_exact"] == EXPECTED_HOLDER_DAYS
            and result["new_market_api_requests_max"] == 10,
            "second_pre_first_low_krx_result_incomplete")
    return result


def valid_second_evidence(public_panels, second, *, provenance=None):
    """Check that saved aggregate matches current independent frozen sources.

    Historical truth is bounded to the independently authenticated 5 dates.
    Source job/artifact provenance is pinned after a successful one-shot run.
    """
    prior = second_batch_plan(public_panels)
    require(type(second) is dict and second.get("schema") == SCHEMA
            and second.get("saved_on_kst") == "2026-11-23"
            and second.get("claim_frozen_readonly") is True,
            "second_pre_first_low_source_not_frozen")
    if provenance is not None:
        require(tuple(second.get(key) for key in (
            "run_id", "job_id", "artifact_id", "artifact_sha256")) == provenance,
            "second_pre_first_low_provenance_mismatch")
    record = second.get("result")
    require(type(record) is dict and record.get("schema") == PROBE_SCHEMA
            and record.get("source_public_input_sha256") == list(SOURCE_DIGESTS)
            and record.get("planning") == prior["report"]
            and tuple(record.get("scope_dates", ())) == EXPECTED_DATES
            and record.get("new_market_api_requests_max") == 10
            and record.get("official_low_holder_days_exact") == EXPECTED_HOLDER_DAYS
            and all(record.get(key) is False for key in (
                "has_verified_all_pre_first_touch_holder_lows",
                "performance_validated", "v30b_release_ready")),
            "second_pre_first_low_price_scope_mismatch")
    official = record.get("official")
    require(type(official) is dict and set(official) == set(EXPECTED_DATES),
            "second_pre_first_low_missing_ohlc_market_day")
    previous_first = json.loads((
        ROOT / "ops/v3-historical-pre-first-low-official-20261123.json"
    ).read_text(encoding="utf-8"))["result"]["official"]
    prior_hashes = {digest for row in previous_first.values()
                    for digest in row["official_source_sha256"].values()}
    new_hashes = set()
    count = 0
    for session, observed in official.items():
        targets = prior["uncovered"][day(session)]
        sha = observed.get("official_source_sha256")
        require(type(sha) is dict and set(sha) == {"KOSPI", "KOSDAQ"}
                and all(type(v) is str
                        and re.fullmatch(r"[0-9a-f]{64}", v)
                        and v not in new_hashes and v not in prior_hashes
                        for v in sha.values())
                and sha["KOSPI"] != sha["KOSDAQ"],
                "second_pre_first_low_source_sha_reused")
        new_hashes.update(sha.values())
        require(observed.get("exact_original_or_successor_pre_first_lows") ==
                len(targets)
                and observed.get("no_prior_official_low_breach_on_this_date") is True
                and type(observed.get("marketwide_total_rows")) is int
                and type(observed.get("marketwide_traded_exact_ohlc")) is int
                and type(observed.get("marketwide_untraded_rows")) is int
                and observed["marketwide_traded_exact_ohlc"] > 2000
                and observed["marketwide_traded_exact_ohlc"] +
                    observed["marketwide_untraded_rows"] ==
                    observed["marketwide_total_rows"],
                "second_pre_first_low_marketwide_ohlc_mismatch")
        count += len(targets)
    require(count == EXPECTED_HOLDER_DAYS, "second_low_holder_day_count_changed")
    confirmed = (saved_official_ohlc_dates()
                 | {day(on) for on in previous_first}
                 | {day(on) for on in official})
    extended = prepare(public_panels, confirmed)
    report = extended["report"]
    require(report["pre_touch_public_traded_holder_days"] == 752
            and report["pre_touch_holder_days_on_existing_official_ohlc_dates"] == 250
            and report["pre_touch_holder_days_without_existing_official_ohlc_date"] == 502
            and report["distinct_pre_touch_official_low_missing_dates"] == 184
            and report["previously_authenticated_official_marketwide_ohlc_dates"] == 54,
            "second_pre_first_low_did_not_reduce_missing_coverage")
    report.update({
        "independently_authenticated_new_low_dates": 10,
        "independently_authenticated_new_pre_touch_holder_lows": 82,
        "prior_official_pre_touch_lows": 168,
        "second_batch_additional_original_successor_lows": count,
        "unverified_pre_touch_holder_lows_remaining": 502,
        "offline_replay_new_krx_market_api_requests": 0,
        "first_touch_earliest_low_certified": False,
        "all_prior_official_lows_covered": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    })
    return extended


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        panels = read_panels(args.sources)
        if args.probe:
            key = os.getenv("KRX_API_KEY", "")
            require(bool(key.strip()), "krx_api_key_unavailable")
            result = probe_second(panels, KRXClient(key))
        elif SECOND_FROZEN.exists():
            second = json.loads(SECOND_FROZEN.read_text(encoding="utf-8"))
            result = valid_second_evidence(panels, second)["report"]
        else:
            result = second_batch_plan(panels)["report"]
        print(json.dumps(result, sort_keys=True))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        reason = str(exc)
        if not re.fullmatch("[a-z][a-z0-9_]{5,99}", reason):
            reason = "unclassified_failure"
        parser.exit(1, "second_pre_first_low_audit_failed:" + reason + "\n")


if __name__ == "__main__":
    main()
