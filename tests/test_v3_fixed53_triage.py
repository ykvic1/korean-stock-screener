"""Offline fixed original 53 price-history triage, without any network."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from baseline_cohort import load_cohort
from v3_fixed53_triage import InvalidFixedCohortEvidence, triage_fixed_53

ROOT = Path(__file__).resolve().parents[1]


def example():
    pinned = load_cohort()
    rows = []
    for i, s in enumerate(pinned):
        if i < 10:
            raw, usable = 2000, 200 + i
        elif i < 16:
            raw, usable = 200 + i, 200 + i
        else:
            raw, usable = 2000, 850 + i
        rows.append({
            "code": s["code"], "name": s["name"],
            "rows": raw, "usable_segment_rows": usable,
            "quarantined_rows": raw - usable,
            "strict_valid": usable >= 650,
            "end": "2026-10-16",
            "cross_validation": {
                "status": "mismatch_or_insufficient",
                "compared_sessions": min(252, usable),
                "matched_sessions": min(250, usable),
                "recent_volume_equal_sessions": min(251, usable),
            },
        })
    return {
        "asof": "2026-10-16",
        "baseline_cohort": {
            "baseline_date": "2026-10-14", "requested": 53,
            "fetched": 53, "numeric_segment_usable": 37,
            "stocks": rows,
        },
    }, pinned


class Fixed53TriageTests(unittest.TestCase):
    def test_pinned_fifty_three_partition_priority_and_null_scoring(self):
        snapshot, pinned = example()
        result = triage_fixed_53(snapshot, pinned)
        self.assertEqual(result["classification"], {
            "quarantined_suffix_short": 10,
            "raw_observed_history_short": 6,
            "numeric_suffix_only": 37,
            "other_unverified": 0,
        })
        self.assertEqual(len(result["rows"]), 53)
        self.assertEqual(len({r["code"] for r in result["rows"]}), 53)
        self.assertEqual(result["rows"][0]["triage_kind"], "quarantined_suffix_short")
        self.assertEqual(result["rows"][10]["triage_kind"], "raw_observed_history_short")
        self.assertEqual(result["rows"][16]["triage_kind"], "numeric_suffix_only")
        self.assertEqual(result["independent_full_history_verified"], 0)
        self.assertEqual(result["regular_session_only_volume_verified"], 0)
        self.assertFalse(result["confirmed_scoring_allowed"])
        self.assertFalse(result["v3_release_ready"])
        self.assertIsNone(result["confirmed_score"])
        self.assertEqual(result["rows"][0]["price_disagreement_sessions"], 2)
        self.assertEqual(result["rows"][0]["volume_disagreement_sessions"], 1)
        self.assertFalse(result["rows"][0]["full_history_verified"])

    def test_changed_membership_duplicate_or_name_fails(self):
        for name in ("missing", "duplicate", "wrong_name", "new_code",
                     "wrong_pinned"):
            snap, cohort = example()
            rows = snap["baseline_cohort"]["stocks"]
            if name == "missing": rows.pop()
            if name == "duplicate": rows[1]["code"] = rows[0]["code"]
            if name == "wrong_name": rows[0]["name"] = "FAKE NAME"
            if name == "new_code": rows[0]["code"] = "999999"
            if name == "wrong_pinned": cohort = cohort[:-1]
            with self.subTest(name=name), self.assertRaises(InvalidFixedCohortEvidence):
                triage_fixed_53(snap, cohort)

    def test_stale_or_contradictory_observations_are_fail_closed(self):
        for name in ("wrong_date", "future_date", "raw_negative", "bad_suffix",
                     "bad_partition", "claimed_strict", "bad_verified_count",
                     "bad_fetched", "bad_status", "bad_price_count", "bad_volume_count",
                     "wrong_baseline", "no_compared"):
            snap, pinned = example()
            r = snap["baseline_cohort"]["stocks"][0]
            if name == "wrong_date": r["end"] = "2026-10-15"
            if name == "future_date": snap["asof"] = "2026-10-17"
            if name == "raw_negative": r["rows"] = -1
            if name == "bad_suffix": r["usable_segment_rows"] = -100
            if name == "bad_partition": r["quarantined_rows"] = 1
            if name == "claimed_strict": r["strict_valid"] = True
            if name == "bad_verified_count":
                snap["baseline_cohort"]["numeric_segment_usable"] = 53
            if name == "bad_fetched": snap["baseline_cohort"]["fetched"] = 52
            if name == "bad_status": r["cross_validation"]["status"] = "CERTIFIED"
            if name == "bad_price_count":
                r["cross_validation"]["matched_sessions"] = 253
            if name == "bad_volume_count":
                r["cross_validation"]["recent_volume_equal_sessions"] = 253
            if name == "wrong_baseline":
                snap["baseline_cohort"]["baseline_date"] = "2026-10-15"
            if name == "no_compared":
                r["cross_validation"]["compared_sessions"] = None
            with self.subTest(name=name), self.assertRaises(InvalidFixedCohortEvidence):
                triage_fixed_53(snap, pinned)

    def test_actual_published_snapshot_stays_noncertifying(self):
        snapshot = json.loads((ROOT / "docs/reliability.json").read_text(encoding="utf-8"))
        result = triage_fixed_53(snapshot, load_cohort())
        self.assertEqual(len(result["rows"]), 53)
        self.assertEqual(sum(result["classification"].values()), 53)
        self.assertEqual(result["numeric_suffix_usable_unverified"],
                         snapshot["baseline_cohort"]["numeric_segment_usable"])
        self.assertEqual(result["independent_full_history_verified"], 0)
        self.assertEqual(result["regular_session_only_volume_verified"], 0)
        self.assertTrue(all(not r["scoring_allowed"] for r in result["rows"]))
        # Raw price/volume comparisons are not official KRX regular-only data.
        self.assertTrue(all(not r["official_krx_regular_price_volume_verified"]
                            for r in result["rows"]))

    def test_cli_private_standalone_json_no_cache_mutation(self):
        run = subprocess.run([sys.executable, "-m", "v3_fixed53_triage"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr[-500:])
        result = json.loads(run.stdout)
        self.assertEqual(result["requested"], 53)
        self.assertEqual(len(result["rows"]), 53)
        self.assertFalse(result["confirmed_scoring_allowed"])


if __name__ == "__main__":
    unittest.main()
