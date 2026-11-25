import json
import unittest

from v3_operational_research_score import VERSION
from v3_score_observation_capture import SCORE_PATH
from v33_r1e_fcf_exclusions import audit_snapshot, prepare


DAY = "2026-11-24"


def row(rank, cash_points, yield_points, *, cap, risk):
    return {
        "market": "KOSPI", "rank": rank, "score": 1.7, "base": 2, "available": 5.5,
        "capital_deduction": cap, "risk_deduction": risk,
        "parts": {
            "financial": {"cashflow_fcf_persistence": [cash_points, 3, DAY]},
            "valuation": {"fcf_yield": [yield_points, 2.5, DAY]},
        },
    }


class FrozenR1eExclusionTests(unittest.TestCase):
    def test_common_population_keeps_observed_zeros_and_breaks_baseline_ties_by_code(self):
        missing = row(3, 2, 0, cap=.1, risk=.2)
        missing["parts"]["valuation"] = {}
        missing["available"] = 3
        snapshot = {
            "asof": DAY, "version": VERSION, "format": "compact-parts-v1", "listed_total": 3,
            "rows": {
                "000002": row(2, 0, 2, cap=.2, risk=.1),
                "000001": row(1, 2, 0, cap=.1, risk=.2),
                "000003": missing,
            },
        }
        cohorts, original = prepare(snapshot)
        self.assertEqual(original, ["000001", "000002", "000003"])
        self.assertEqual(cohorts["baseline"], ["000001", "000002"])
        self.assertEqual(cohorts["omit_fcf_persistence"], ["000002", "000001"])
        self.assertEqual(cohorts["omit_fcf_yield"], ["000001", "000002"])

        snapshot["rows"]["000002"]["rank"] = 1
        with self.assertRaisesRegex(ValueError, "rank does not reconstruct"):
            prepare(snapshot)
        snapshot["rows"]["000002"]["rank"] = 2
        snapshot["rows"]["000002"]["capital_deduction"] = 1
        with self.assertRaisesRegex(ValueError, "score does not reconstruct"):
            prepare(snapshot)

    def test_actual_frozen_baseline_and_common_cohort(self):
        result = audit_snapshot(SCORE_PATH)
        self.assertFalse(result["performance_validated"])
        self.assertFalse(result["reweighting_authorized"])
        self.assertGreater(result["common_eligible_count"], 0)
        self.assertEqual(sum(result["common_eligible_by_market"].values()),
                         result["common_eligible_count"])
        self.assertEqual(result["common_eligible_count"] + result["excluded_ranked_count"],
                         result["baseline_ranks_reconstructed"])
        print("V33_FCF_EXCLUSIONS=" + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
