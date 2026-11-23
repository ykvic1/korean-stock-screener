"""Replay final KRX pre-touch LOW evidence on the fixed historical cohort."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from v3_historical_pretouch_official_low_fourth import (
    FOURTH_FROZEN, ROOT, day, read_panels, require, verified_plan,
)
from v3_historical_pretouch_official_low_fifth import (
    FROZEN, SIXTH_FROZEN, SEVENTH_FROZEN, replay,
)


PROVENANCE = (45123850602, 137523566200)
FROZEN_FINAL = ROOT / "ops/v3-historical-pre-first-low-official-final-20261123.json"


def replay_final(panels, evidence):
    require((evidence["run_id"], evidence["job_id"]) == PROVENANCE,
            "final_low_provenance_mismatch")
    fourth, fifth, sixth, seventh = (
        json.loads(path.read_text(encoding="utf-8"))
        for path in (FOURTH_FROZEN, FROZEN, SIXTH_FROZEN, SEVENTH_FROZEN))
    baseline = replay(panels, fifth, sixth, seventh)
    plan, seen = verified_plan(panels)
    prior = [fourth["result"]] + [
        batch for proof in (fifth, sixth, seventh) for batch in proof["result"]["batches"]]
    excluded = {day(on) for batch in prior for on in batch["scope_dates"]}
    seen.update(digest for batch in prior for row in batch["official"].values()
                for digest in row["official_source_sha256"].values())
    pending = set(plan["uncovered"]) - excluded
    rows = evidence["sessions"]
    summary = evidence["summary"]
    failed = {day(row["date"]) for row in summary["failed"]}
    verified_days = {day(row["date"]) for row in rows}
    require(failed.isdisjoint(verified_days) and failed | verified_days == pending
            and len(verified_days) == len(rows) and len(failed) == len(summary["failed"])
            and summary["requested_sessions"] == len(pending), "final_low_dates_mismatch")
    added = 0
    for item in rows:
        row = item["proof"]
        targets = plan["uncovered"][day(item["date"])]
        hashes = row["official_source_sha256"]
        require(set(hashes) == {"KOSPI", "KOSDAQ"} and len(set(hashes.values())) == 2
                and all(re.fullmatch(r"[0-9a-f]{64}", digest) and digest not in seen
                        for digest in hashes.values())
                and row["exact_original_or_successor_pre_first_lows"] == len(targets)
                and row["no_prior_official_low_breach_on_this_date"] is True
                and row["marketwide_traded_exact_ohlc"] > 2000
                and row["marketwide_traded_exact_ohlc"] + row["marketwide_untraded_rows"]
                    == row["marketwide_total_rows"], "final_low_row_mismatch")
        seen.update(hashes.values())
        added += len(targets)
    verified = baseline["verified_holder_days"] + added
    unverified = sum(len(plan["uncovered"][on]) for on in failed)
    require(verified + unverified == 752
            and added == summary["new_verified_holder_days"]
            and verified == summary["verified_holder_days"]
            and unverified == summary["unverified_holder_days"], "final_low_totals_mismatch")
    return {"verified_holder_days": verified, "unverified_holder_days": unverified,
            "all_eligible_traded_pre_touch_lows_verified": unverified == 0,
            "replay_provider_calls": 0, "v30b_release_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    args = parser.parse_args()
    evidence = json.loads(FROZEN_FINAL.read_text(encoding="utf-8"))
    print(json.dumps(replay_final(read_panels(args.sources), evidence), sort_keys=True))


if __name__ == "__main__":
    main()
