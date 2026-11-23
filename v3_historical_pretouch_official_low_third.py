"""Third historical KRX intraday LOW evidence: two strictly bounded five-day batches.

Replays first and second authenticated 5-date KRX proofs without network.
A one-shot explicit --probe executes two 5-session batches (at most 20
official market API requests overall), conditioned on each previous batch's
successful all-market OHLC and holding-unit adjusted LOW verification.

The existing 245-day archived CLOSE evidence is never misclassified as
independently authenticated LOW. No issuer symbols, raw prices or support
thresholds in the published JSON.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from official_probe.clients import KRXClient
from v3_historical_market_coverage import ROOT, day, read_panels, require
from v3_historical_pilot_official_prices import SOURCE_DIGESTS
from v3_historical_pretouch_official_low_gap import (
    BATCH_LIMIT, PROBE_SCHEMA, prepare, saved_official_ohlc_dates,
    check_bounded_batch,
)
from v3_historical_pretouch_official_low_second import (
    SECOND_FROZEN, valid_second_evidence, SECOND_PROVENANCE,
)

THIRD_FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-third-20261123.json"
FIRST_FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-20261123.json"
SCHEMA = "v3-historical-pre-first-low-official-third-frozen-v1"
PROBE_BATCH_SCHEMA = "v3-historical-pre-first-low-third-two-batches-v1"
FIRST_FIVE = (
    "2025-12-10", "2025-12-11", "2025-12-16",
    "2025-12-17", "2025-12-19",
)
FIRST_EXPECTED_HOLDER_LOWS = 30
MAX_NEW_MARKET_API_REQUESTS = 20
# Pinned after actual independently successful one-shot KRX source verification.
THIRD_PROVENANCE = (
    45107523222, 137472099269, 14788107574,
    "ffeb52e1fecdf19601bfdead39999cc4fbc532bc77774b9c160063fb01adbdca",
)


def previous_verified(panels):
    """Reauthenticate all existing 250 issuer/date LOW cells, provider-free."""
    second = json.loads(SECOND_FROZEN.read_text(encoding="utf-8"))
    previous = valid_second_evidence(panels, second)
    report = previous["report"]
    require(report["pre_touch_public_traded_holder_days"] == 752
            and report["pre_touch_holder_days_on_existing_official_ohlc_dates"] == 250
            and report["pre_touch_holder_days_without_existing_official_ohlc_date"] == 502
            and report["previously_authenticated_official_marketwide_ohlc_dates"] == 54
            and report["distinct_pre_touch_official_low_missing_dates"] == 184
            and tuple(report["bounded_next_official_request_dates"]) == FIRST_FIVE
            and report["bounded_next_issuer_low_observations"] == FIRST_EXPECTED_HOLDER_LOWS
            and len(previous["batch"]) == BATCH_LIMIT,
            "third_pre_first_low_prior_official_scope_changed")
    first = json.loads(FIRST_FROZEN.read_text(encoding="utf-8"))
    require(first.get("schema") == "v3-historical-pre-first-low-official-frozen-v1"
            and type(first.get("result", {}).get("official")) is dict,
            "missing_original_first_batch_official_sources")
    previous_sources = {
        h for frozen in (first, second)
        for evidence in frozen["result"]["official"].values()
        for h in evidence["official_source_sha256"].values()
    }
    require(len(previous_sources) == 20, "reused_previous_krx_ohlc_source_hash")
    known_days = (saved_official_ohlc_dates()
                  | {day(k) for k in first["result"]["official"]}
                  | {day(k) for k in second["result"]["official"]})
    require(len(known_days) == 54, "third_prior_marketday_scope_mismatch")
    return previous, known_days, previous_sources


def probe_ten_new_official_days(panels, client):
    """Two independent five-session batches, never beyond 20 API calls."""
    require(not THIRD_FROZEN.exists(),
            "third_official_low_evidence_already_frozen_no_repeat_queries")
    first_plan, known, prior_hashes = previous_verified(panels)
    first = check_bounded_batch(panels, first_plan, client)
    require(tuple(first["scope_dates"]) == FIRST_FIVE
            and first["official_low_holder_days_exact"] == FIRST_EXPECTED_HOLDER_LOWS
            and first["new_market_api_requests_max"] == 10,
            "first_five_third_batch_official_ohlc_not_complete")
    seen = {v for item in first["official"].values()
            for v in item["official_source_sha256"].values()}
    require(len(seen) == 10 and seen.isdisjoint(prior_hashes),
            "third_first_five_official_source_reused")
    next_plan = prepare(panels, known | {day(k) for k in first["scope_dates"]})
    require(len(next_plan["batch"]) == BATCH_LIMIT
            and tuple(d.isoformat() for d in next_plan["batch"]) != FIRST_FIVE,
            "second_five_third_batch_not_selected")
    second = check_bounded_batch(panels, next_plan, client)
    more = {v for item in second["official"].values()
            for v in item["official_source_sha256"].values()}
    require(len(more) == 10 and more.isdisjoint(prior_hashes | seen)
            and not (set(second["scope_dates"]) & set(first["scope_dates"])),
            "third_second_five_krx_official_source_reused")
    return {
        "schema": PROBE_BATCH_SCHEMA,
        "source_public_input_sha256": list(SOURCE_DIGESTS),
        "previously_authenticated_holder_lows": 250,
        "two_five_day_batches": [first, second],
        "new_official_traded_holder_lows":
            first["official_low_holder_days_exact"] +
            second["official_low_holder_days_exact"],
        "new_official_market_days": 10,
        "maximum_official_krx_market_requests": MAX_NEW_MARKET_API_REQUESTS,
        "first_touch_earliest_low_certified": False,
        "all_prior_official_lows_covered": False,
        "regular_session_volume_independently_verified": False,
        "historical_pit_scores_certified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def verify_third_official_coverage(panels, frozen, *, provenance=THIRD_PROVENANCE):
    """Replay KRX evidence aggregate, frozen previous inputs & all coverage math."""
    prior, known, prior_hashes = previous_verified(panels)
    require(type(frozen) is dict and frozen.get("schema") == SCHEMA
            and frozen.get("saved_on_kst") == "2026-11-23"
            and frozen.get("claim_frozen_readonly") is True,
            "third_official_low_frozen_source_invalid")
    require(provenance is not None and
            tuple(frozen.get(k) for k in (
                "run_id", "job_id", "artifact_id", "artifact_sha256")) == provenance
            and frozen.get("run_url") ==
                ("https://github.com/ykvic1/korean-stock-screener/actions/runs/"
                 + str(provenance[0])),
            "third_official_low_unauthenticated_source_provenance")
    proof = frozen.get("result")
    require(type(proof) is dict and proof.get("schema") == PROBE_BATCH_SCHEMA
            and proof.get("source_public_input_sha256") == list(SOURCE_DIGESTS)
            and proof.get("previously_authenticated_holder_lows") == 250
            and proof.get("new_official_market_days") == 10
            and proof.get("maximum_official_krx_market_requests") == 20
            and all(proof.get(k) is False for k in (
                "first_touch_earliest_low_certified",
                "all_prior_official_lows_covered",
                "regular_session_volume_independently_verified",
                "historical_pit_scores_certified",
                "performance_validated", "v30b_release_ready")),
            "third_krx_official_report_scope_changed")
    batches = proof.get("two_five_day_batches")
    require(type(batches) is list and len(batches) == 2,
            "third_official_krx_batches_incomplete")
    requested = 0
    source_fingerprints = set(prior_hashes)
    for batch_number, batch in enumerate(batches):
        require(type(batch) is dict and batch.get("schema") == PROBE_SCHEMA
                and batch.get("planning") == prior["report"]
                and batch.get("source_public_input_sha256") == list(SOURCE_DIGESTS)
                and batch.get("new_market_api_requests_max") == 10
                and type(batch.get("official")) is dict
                and type(batch.get("scope_dates")) is list
                and len(batch["scope_dates"]) == 5
                and len(set(batch["scope_dates"])) == 5
                and set(batch["official"]) == set(batch["scope_dates"])
                and all(batch.get(k) is False for k in (
                    "has_verified_all_pre_first_touch_holder_lows",
                    "performance_validated", "v30b_release_ready")),
                "third_bad_krx_batch_replay_or_source")
        require(batch["scope_dates"] ==
                [d.isoformat() for d in prior["batch"]],
                "third_five_day_source_date_priority_changed")
        if batch_number == 0:
            require(tuple(batch["scope_dates"]) == FIRST_FIVE
                    and batch.get("official_low_holder_days_exact") == 30,
                    "third_first_batch_30_holder_lows_missing")
        count = 0
        for on, observed in batch["official"].items():
            targets = prior["uncovered"][day(on)]
            digests = observed.get("official_source_sha256")
            require(type(digests) is dict
                    and set(digests) == {"KOSPI", "KOSDAQ"}
                    and all(type(value) is str
                            and re.fullmatch("[0-9a-f]{64}", value)
                            and value not in source_fingerprints
                            for value in digests.values())
                    and digests["KOSPI"] != digests["KOSDAQ"],
                    "third_krx_market_hash_reused_or_missing")
            source_fingerprints.update(digests.values())
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
                    "third_five_day_official_holder_low_mismatch")
            count += len(targets)
        require(count == batch["official_low_holder_days_exact"],
                "third_frozen_holder_low_count_mismatch")
        requested += count
        known |= {day(d) for d in batch["scope_dates"]}
        prior = prepare(panels, known)
    require(requested == proof.get("new_official_traded_holder_lows")
            and len(source_fingerprints) == 40
            and len(known) == 64,
            "third_ten_day_source_or_holder_count_mismatch")
    report = prior["report"]
    require(report["pre_touch_public_traded_holder_days"] == 752
            and report["pre_touch_holder_days_on_existing_official_ohlc_dates"]
                == 250 + requested
            and report["pre_touch_holder_days_without_existing_official_ohlc_date"]
                == 502 - requested
            and report["distinct_pre_touch_official_low_missing_dates"] == 174
            and report["previously_authenticated_official_marketwide_ohlc_dates"] == 64,
            "third_ten_day_official_coverage_did_not_reduce_gap")
    report.update({
        "independently_authenticated_new_low_dates": 20,
        "independently_authenticated_new_pre_touch_holder_lows": 82 + requested,
        "prior_official_pre_touch_lows": 168,
        "second_batch_additional_original_successor_lows": 35,
        "third_batch_additional_original_successor_lows": requested,
        "third_batch_new_official_market_days": 10,
        "unverified_pre_touch_holder_lows_remaining": 502 - requested,
        "offline_replay_new_krx_market_api_requests": 0,
        "first_touch_earliest_low_certified": False,
        "all_prior_official_lows_covered": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    })
    return prior


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        panels = read_panels(args.sources)
        if args.probe:
            require(os.environ.get("KRX_API_KEY", "").strip(),
                    "third_krx_secret_not_available")
            result = probe_ten_new_official_days(
                panels, KRXClient(os.environ["KRX_API_KEY"]))
        elif THIRD_FROZEN.exists():
            result = verify_third_official_coverage(
                panels, json.loads(THIRD_FROZEN.read_text(encoding="utf-8"))
            )["report"]
        else:
            result = previous_verified(panels)[0]["report"]
        print(json.dumps(result, sort_keys=True))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        reason = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{5,99}", reason):
            reason = "unclassified_input_error"
        parser.exit(1, "third_krx_official_low_evidence_failed:" + reason + "\n")


if __name__ == "__main__":
    main()
