"""Assert post-close V3 observability cannot mutate the legacy publication."""
import unittest
from pathlib import Path


class PostCloseSourceAuditWorkflowTests(unittest.TestCase):
    def test_readonly_workflow_only_runs_after_successful_scheduled_batch(self):
        source = Path(".github/workflows/v3-six-pillar-source-readiness.yml")
        text = source.read_text(encoding="utf-8")
        self.assertIn("workflows: ['Full-Market 100-Stock Batches']", text)
        self.assertIn("types: [completed]", text)
        self.assertIn("github.event.workflow_run.event == 'schedule'", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("ref: main", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python v3_six_pillar_source_readiness.py", text)
        self.assertIn("retention-days: 14", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertNotIn("actions/download-artifact", text)
        self.assertNotIn("DART_API_KEY", text)
        self.assertNotIn("KRX_API_KEY", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("screener.py --", text)

    def test_source_audit_cannot_change_the_daily_schedule(self):
        full_market = Path(".github/workflows/full-market-batches.yml")
        text = full_market.read_text(encoding="utf-8")
        self.assertIn("cron: '30 9 * * 1-5'", text)
        self.assertIn("Full-Market 100-Stock Batches", text)


if __name__ == "__main__":
    unittest.main()
