"""Scheduled four-stage KRX derived replay stays bounded and aligned (offline)."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"


def crons(filename):
    text = (WF / filename).read_text(encoding="utf-8")
    values = re.findall(r"^\s+- cron: '([^']+)'", text, re.MULTILINE)
    return text, values


class FourSlotDerivedPipelineTests(unittest.TestCase):
    def test_daily_pipeline_has_exact_ordered_four_utc_slots(self):
        expectations = (
            ("v3-krx-derived-history.yml",
             ["11 17 * * *", "11 22 * * *", "11 5 * * *", "11 13 * * *"]),
            ("v3-krx-derived-boundaries.yml",
             ["47 17 * * *", "47 22 * * *", "47 5 * * *", "47 13 * * *"]),
            ("v3-krx-derived-rolling-returns.yml",
             ["11 18 * * *", "11 23 * * *", "11 6 * * *", "11 14 * * *"]),
            ("v3-krx-derived-corporate-risk-incremental.yml",
             ["43 18 * * *", "43 23 * * *", "43 6 * * *", "43 14 * * *"]),
        )
        for name, expected in expectations:
            with self.subTest(workflow=name):
                source, actual = crons(name)
                self.assertEqual(actual, expected)
                self.assertIn("group: v3-krx-private-derived-history", source)
                self.assertIn("cancel-in-progress: false", source)
                self.assertIn("contents: read", source)
                self.assertNotIn("contents: write", source)
                self.assertIn("persist-credentials: false", source)

    def test_no_unbounded_market_calls_or_certified_scores(self):
        source = (ROOT / "v3_krx_derived_backfill.py").read_text(encoding="utf-8")
        bridge = (ROOT / "v3_krx_derived_bridges.py").read_text(encoding="utf-8")
        rolling = (ROOT / "v3_krx_derived_rolling_returns.py").read_text(encoding="utf-8")
        quality = (ROOT / "v3_krx_corporate_risk_incremental.py").read_text(encoding="utf-8")
        self.assertIn("MAX_WINDOWS_PER_RUN=16", source)
        self.assertIn("MAX_SOURCE_CALLS=MAX_WINDOWS_PER_RUN*10", source)
        self.assertIn("MAX_LINKS_PER_RUN=24", bridge)
        self.assertIn("MAX_PER_HORIZON=24", rolling)
        self.assertIn("MAX_DELTA_COHORTS=16", quality)
        self.assertEqual(4 * (16 * 10 + 24 * 4), 1024)
        roll_wf = (WF / "v3-krx-derived-rolling-returns.yml").read_text()
        risk_wf = (WF / "v3-krx-derived-corporate-risk-incremental.yml").read_text()
        self.assertNotIn("KRX_API_KEY:", roll_wf)
        self.assertNotIn("KRX_API_KEY:", risk_wf)
        full_market = (WF / "full-market-batches.yml").read_text()
        self.assertIn("cron: '30 9 * * 1-5'", full_market)


if __name__ == "__main__":
    unittest.main()
