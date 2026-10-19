"""Guard both automatic quant workflows against stale V3 ranked-candidate data.

The user sees per-candidate quant. This MUST be regenerated from same-date
researched symbols after full-market and Saturday quant runs, without a
second OHLCV vendor fetch or claiming predictive validation.
"""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATHS=(
    ".github/workflows/quant.yml",
    ".github/workflows/v3-ranked-quant-refresh.yml",
)

class V3RankedQuantWorkflowTests(unittest.TestCase):
    def test_both_workflows_republish_candidate_priority_same_day(self):
        for path in PATHS:
            with self.subTest(path=path):
                script=(ROOT/path).read_text(encoding="utf-8")
                self.assertIn("python v3_priority_quant_feed.py",script)
                self.assertIn("--market docs/data.json",script)
                self.assertIn("--recommendations docs/recommendations.json",script)
                self.assertIn("--quant docs/quant.json",script)
                self.assertIn("--output docs/v3-priority-quant.json",script)
                self.assertIn("git add docs/quant.json",script)
                self.assertIn("docs/v3-priority-quant.json",script)
                self.assertIn("candidate",script)
                self.assertIn("build_priority_feed(market,rec,study)",script)
                self.assertIn("stale_ranked_priority_quant_after_rebase",script)
                self.assertIn("git pull --rebase origin main",script)
                self.assertIn("git push origin HEAD:main",script)
                self.assertIn("group: korea-stock-screener-publication",script)
                self.assertIn("cancel-in-progress: false",script)

    def test_nightly_workflow_runs_only_after_successful_market_job(self):
        nightly=(ROOT/PATHS[0]).read_text(encoding="utf-8")
        self.assertIn("- Full-Market 100-Stock Batches",nightly)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",nightly)
        self.assertIn("if: steps.engine.outcome == 'success'",nightly)
        self.assertIn("Quant v2.1",nightly)
        self.assertIn("docs/v3-quant-coverage.json",nightly)

    def test_no_public_raw_ohlcv_or_fake_validation_in_publishing_steps(self):
        for path in PATHS:
            with self.subTest(path=path):
                text=(ROOT/path).read_text(encoding="utf-8")
                for raw in ("git add cache/quant_history","git add cache/","git add raw_ohlcv"):
                    self.assertNotIn(raw,text)
                self.assertNotIn("prediction_validated: true",text)
                self.assertIn("docs/v3-priority-quant.json",text)

if __name__=="__main__":
    unittest.main()
