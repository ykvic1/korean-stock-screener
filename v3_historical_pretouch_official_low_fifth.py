"""Replay the next two exact KRX LOW batches against the frozen input panels."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from v3_historical_pilot_official_prices import SOURCE_DIGESTS
from v3_historical_pretouch_official_low_fourth import (
    FOURTH_FROZEN, ROOT, day, read_panels, require, verified_plan,
    verify_fourth_official_coverage,
)
from v3_historical_pretouch_official_low_gap import PROBE_SCHEMA, choose_batch

PROVENANCE = (45120255470, 137512148468)
FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-fifth-20261123.json"


def replay(panels, proof):
    fourth = json.loads(FOURTH_FROZEN.read_text(encoding="utf-8"))
    baseline = verify_fourth_official_coverage(panels, fourth)
    plan, seen = verified_plan(panels)
    result = proof["result"]
    require(len(result["batches"]) == 2 and result["maximum_market_api_requests"] == 20
            and result["performance_validated"] is False
            and result["v30b_release_ready"] is False, "fifth_low_batch_scope_changed")
    excluded = {day(on) for on in fourth["result"]["scope_dates"]}
    seen.update(
        digest for row in fourth["result"]["official"].values()
        for digest in row["official_source_sha256"].values()
    )
    added = 0
    for batch in result["batches"]:
        planned = choose_batch(plan["uncovered"], excluded)
        dates = [on.isoformat() for on in planned]
        require(batch["schema"] == PROBE_SCHEMA and batch["planning"] == plan["report"]
                and batch["source_public_input_sha256"] == list(SOURCE_DIGESTS)
                and batch["scope_dates"] == dates and set(batch["official"]) == set(dates)
                and batch["new_market_api_requests_max"] == 10
                and batch["has_verified_all_pre_first_touch_holder_lows"] is False
                and batch["performance_validated"] is False
                and batch["v30b_release_ready"] is False, "fifth_low_priority_or_scope_changed")
        batch_count = 0
        for on in planned:
            row = batch["official"][on.isoformat()]
            count = len(plan["uncovered"][on])
            digests = row["official_source_sha256"]
            require(set(digests) == {"KOSPI", "KOSDAQ"}
                    and len(set(digests.values())) == 2
                    and all(re.fullmatch(r"[0-9a-f]{64}", value) and value not in seen
                            for value in digests.values())
                    and row["exact_original_or_successor_pre_first_lows"] == count
                    and row["no_prior_official_low_breach_on_this_date"] is True
                    and row["marketwide_traded_exact_ohlc"] > 2000
                    and row["marketwide_traded_exact_ohlc"] + row["marketwide_untraded_rows"]
                        == row["marketwide_total_rows"], "fifth_low_evidence_row_mismatch")
            seen.update(digests.values())
            batch_count += count
        require(batch_count == batch["official_low_holder_days_exact"],
                "fifth_low_batch_count_mismatch")
        excluded.update(planned)
        added += batch_count

    verified = baseline["verified_holder_days"] + added
    unverified = baseline["unverified_holder_days"] - added
    require((added, verified, unverified) == (
        result["new_verified_holder_days"], result["verified_holder_days"],
        result["unverified_holder_days"]), "fifth_low_total_mismatch")
    return {"new_verified_holder_days": added, "verified_holder_days": verified,
            "unverified_holder_days": unverified, "replay_provider_calls": 0,
            "first_touch_earliest_low_certified": False, "v30b_release_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    args = parser.parse_args()
    proof = json.loads(FROZEN.read_text(encoding="utf-8"))
    require((proof["run_id"], proof["job_id"]) == PROVENANCE,
            "fifth_low_provenance_mismatch")
    print(json.dumps(replay(read_panels(args.sources), proof), sort_keys=True))


if __name__ == "__main__":
    main()
