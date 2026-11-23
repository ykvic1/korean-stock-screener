"""Check the next five previously unqueried KRX pre-first-touch LOW dates."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from official_probe.clients import KRXClient
from v3_historical_market_coverage import ROOT, day, read_panels, require
from v3_historical_pretouch_official_low_gap import PROBE_SCHEMA, check_bounded_batch
from v3_historical_pretouch_official_low_second import SECOND_FROZEN
from v3_historical_pretouch_official_low_third import (
    FIRST_FROZEN,
    THIRD_FROZEN,
    verify_third_official_coverage,
)

FOURTH_FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-fourth-20261123.json"
NEXT_DATES = ("2026-01-02", "2026-01-06", "2026-02-12", "2026-02-19", "2026-02-20")
EXPECTED_HOLDER_LOWS = 21
PROVENANCE = (
    45112064445, 137486434281, 14789629317,
    "83386555ffffdbb8730e41c445709c4fcf3d2f7fb5a1b11038f1049dbf166142",
)


def verified_plan(panels):
    third = json.loads(THIRD_FROZEN.read_text(encoding="utf-8"))
    plan = verify_third_official_coverage(panels, third)
    report = plan["report"]
    require(report["pre_touch_public_traded_holder_days"] == 752
            and report["pre_touch_holder_days_on_existing_official_ohlc_dates"] == 308
            and report["pre_touch_holder_days_without_existing_official_ohlc_date"] == 444
            and tuple(d.isoformat() for d in plan["batch"]) == NEXT_DATES
            and report["bounded_next_issuer_low_observations"] == EXPECTED_HOLDER_LOWS,
            "fourth_official_low_plan_changed")

    earlier_batches = [
        json.loads(path.read_text(encoding="utf-8"))["result"]
        for path in (FIRST_FROZEN, SECOND_FROZEN)
    ] + third["result"]["two_five_day_batches"]
    seen = {
        digest for batch in earlier_batches
        for evidence in batch["official"].values()
        for digest in evidence["official_source_sha256"].values()
    }
    require(len(seen) == 40, "fourth_prior_source_hashes_incomplete")
    return plan, seen


def verify_fourth_official_coverage(panels, frozen):
    require(tuple(frozen.get(k) for k in ("run_id", "job_id", "artifact_id", "artifact_sha256"))
            == PROVENANCE, "fourth_official_low_provenance_mismatch")
    plan, seen = verified_plan(panels)
    result = frozen["result"]
    require(result["schema"] == PROBE_SCHEMA and result["planning"] == plan["report"]
            and tuple(result["scope_dates"]) == NEXT_DATES
            and set(result["official"]) == set(NEXT_DATES)
            and result["new_market_api_requests_max"] == 10
            and all(result.get(k) is False for k in (
                "has_verified_all_pre_first_touch_holder_lows",
                "performance_validated", "v30b_release_ready")),
            "fourth_official_low_evidence_scope_mismatch")
    added = 0
    for on in NEXT_DATES:
        row = result["official"][on]
        count = len(plan["uncovered"][day(on)])
        digests = row["official_source_sha256"]
        require(set(digests) == {"KOSPI", "KOSDAQ"}
                and len(set(digests.values())) == 2
                and all(re.fullmatch(r"[0-9a-f]{64}", value) and value not in seen
                        for value in digests.values())
                and row["exact_original_or_successor_pre_first_lows"] == count
                and row["no_prior_official_low_breach_on_this_date"] is True
                and row["marketwide_traded_exact_ohlc"] > 2000
                and row["marketwide_traded_exact_ohlc"] + row["marketwide_untraded_rows"]
                    == row["marketwide_total_rows"],
                "fourth_official_low_evidence_row_mismatch")
        seen.update(digests.values())
        added += count
    require(added == result["official_low_holder_days_exact"] == EXPECTED_HOLDER_LOWS,
            "fourth_official_low_count_mismatch")
    return {
        "prior_verified_holder_days": 308,
        "new_verified_holder_days": added,
        "verified_holder_days": 308 + added,
        "unverified_holder_days": 444 - added,
        "replay_provider_calls": 0,
        "first_touch_earliest_low_certified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def probe_next_official_days(panels, client):
    require(not FOURTH_FROZEN.exists(), "fourth_official_low_evidence_already_frozen")
    plan, seen = verified_plan(panels)
    result = check_bounded_batch(panels, plan, client)
    new_digests = {
        digest for evidence in result["official"].values()
        for digest in evidence["official_source_sha256"].values()
    }
    require(tuple(result["scope_dates"]) == NEXT_DATES
            and result["official_low_holder_days_exact"] == EXPECTED_HOLDER_LOWS
            and result["new_market_api_requests_max"] == 10
            and len(new_digests) == 10 and new_digests.isdisjoint(seen),
            "fourth_official_low_result_scope_or_source_changed")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        panels = read_panels(args.sources)
        if args.probe:
            key = os.environ.get("KRX_API_KEY", "").strip()
            require(key, "fourth_krx_secret_not_available")
            result = probe_next_official_days(panels, KRXClient(key))
        else:
            frozen = json.loads(FOURTH_FROZEN.read_text(encoding="utf-8"))
            result = verify_fourth_official_coverage(panels, frozen)
        print(json.dumps(result, sort_keys=True))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        reason = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{5,99}", reason):
            reason = "unclassified_input_error"
        parser.exit(1, "fourth_krx_official_low_evidence_failed:" + reason + "\n")


if __name__ == "__main__":
    main()
