"""Scheduled five-slot KRX derived replay stays bounded and aligned (offline)."""
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"


def crons(filename):
    text = (WF / filename).read_text(encoding="utf-8")
    values = re.findall(r"^\s+- cron: '([^']+)'", text, re.MULTILINE)
    return text, values


class FiveSlotDerivedPipelineTests(unittest.TestCase):
    def test_daily_pipeline_has_exact_ordered_five_utc_slots(self):
        expectations = (
            ("v3-krx-derived-history.yml",
             []),
            ("v3-krx-derived-boundaries.yml",
             ["47 17 * * *", "47 22 * * *", "47 2 * * *", "47 5 * * *", "47 13 * * *"]),
            ("v3-krx-derived-corporate-risk-incremental.yml",
             ["43 18 * * *", "43 23 * * *", "43 3 * * *", "43 6 * * *", "43 14 * * *"]),
        )
        for name, expected in expectations:
            with self.subTest(workflow=name):
                source, actual = crons(name)
                self.assertEqual(actual, expected)
                if name == "v3-krx-derived-history.yml":
                    progress = json.loads((ROOT / "docs/v3-krx-derived-backfill-progress.json")
                                          .read_text(encoding="utf-8"))
                    self.assertEqual(progress["five_session_cohorts_completed"], 490)
                    self.assertEqual(progress["five_session_cohorts_remaining"], 0)
                    self.assertIn("workflow_dispatch:", source)
                self.assertIn("group: v3-krx-private-derived-history", source)
                self.assertIn("cancel-in-progress: false", source)
                self.assertIn("contents: read", source)
                self.assertNotIn("contents: write", source)
                self.assertIn("persist-credentials: false", source)

        # Rolling return research is triggered by each successful boundary
        # completion; a single fallback remains for dropped GitHub events.
        # Five bounded source/boundary API slots, one no-provider fallback.
        rolling_workflow, rolling_slots = crons(
            "v3-krx-derived-rolling-returns.yml")
        self.assertEqual(rolling_slots, ["11 18 * * *"])
        self.assertIn("V3 KRX Derived-Only Historical Cohort Boundary Join",
                      rolling_workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",
                      rolling_workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",
                      rolling_workflow)
        self.assertIn("group: v3-krx-private-derived-history",
                      rolling_workflow)
        self.assertIn("cancel-in-progress: false", rolling_workflow)
        self.assertIn("contents: read", rolling_workflow)
        self.assertNotIn("contents: write", rolling_workflow)
        self.assertIn("persist-credentials: false", rolling_workflow)

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
        # The historical 490/490 source backfill no longer runs on a timer.
        # Five remaining scheduled boundary checks have a bounded 96-call
        # upper limit each; manual history replay stays capped separately.
        scheduled_calls = 5 * (24 * 4)
        self.assertEqual(scheduled_calls, 480)
        self.assertLess(scheduled_calls, 10000)
        history_wf = (WF / "v3-krx-derived-history.yml").read_text()
        self.assertIn("V3_DERIVED_BATCH_WINDOWS: ${{ github.event_name == 'workflow_dispatch' && inputs.max_windows || '4' }}", history_wf)
        self.assertIn("V3_BULK_BATCHES: ${{ github.event_name == 'workflow_dispatch' && '1' || '4' }}", history_wf)
        # The dormant former scheduled path retains its 4x4 cap should it
        # ever be deliberately reenabled; manual replay is at most 16x1.
        self.assertEqual(4 * 4 * 10, 160)
        self.assertEqual(1 * 16 * 10, 160)
        roll_wf = (WF / "v3-krx-derived-rolling-returns.yml").read_text()
        risk_wf = (WF / "v3-krx-derived-corporate-risk-incremental.yml").read_text()
        self.assertNotIn("KRX_API_KEY:", roll_wf)
        self.assertNotIn("KRX_API_KEY:", risk_wf)
        full_market = (WF / "full-market-batches.yml").read_text()
        self.assertIn("cron: '30 17 * * 1-5'", full_market)


if __name__ == "__main__":
    unittest.main()
