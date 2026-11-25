from hashlib import sha256
import json
from pathlib import Path
import unittest

from v3_operational_research_score import VERSION
from v3_score_observation_capture import SCORE_PATH
from v33_r1e_factor_overlap import audit_snapshot, summarize


DAY = "2026-11-24"


class R1eOverlapTests(unittest.TestCase):
    def test_zero_is_observed_and_partial_future_and_unranked_are_excluded(self):
        def row(rank, market, points, weight=2, asof=DAY):
            return {
                "rank": rank,
                "market": market,
                "parts": {
                    "volume": {"weekly_structure_increment": [points, weight, asof]},
                    "technical": {"weekly_pattern": [points, 9.6, DAY]},
                },
            }

        rows = {str(i): row(i + 1, "KOSPI", i % 2) for i in range(30)}
        rows.update({
            "partial": row(31, "KOSPI", 1, weight=1),
            "future": row(32, "KOSPI", 1, asof="2026-11-25"),
            "unranked": row(None, "KOSPI", 0),
            "kosdaq": row(1, "KOSDAQ", 1),
        })
        summary = summarize({
            "asof": DAY, "version": VERSION, "format": "compact-parts-v1", "rows": rows,
        })
        kospi = summary["markets"]["KOSPI"]
        pair = kospi["pairs"][0]
        self.assertEqual(kospi["ranked_count"], 32)
        self.assertEqual(pair["complete_pair_count"], 30)
        self.assertEqual(pair["spearman_rho"], 1)
        self.assertTrue(pair["overlap_warning"])
        kosdaq = summary["markets"]["KOSDAQ"]["pairs"][0]
        self.assertEqual(kosdaq["complete_pair_count"], 1)
        self.assertEqual(kosdaq["status"], "insufficient_pairs")
        self.assertIsNone(kosdaq["spearman_rho"])

    def test_actual_saved_snapshot_schema_and_population(self):
        result = audit_snapshot(SCORE_PATH)
        self.assertEqual(result["source_sha256"], sha256(Path(SCORE_PATH).read_bytes()).hexdigest())
        self.assertFalse(result["reweighting_authorized"])
        for market in result["markets"].values():
            self.assertGreater(market["ranked_count"], 0)
            for pair in market["pairs"]:
                self.assertLessEqual(pair["complete_pair_count"], market["ranked_count"])
        print("V33_R1E_OVERLAP=" + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
