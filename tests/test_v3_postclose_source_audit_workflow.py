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
        self.assertIn("cron: '30 12 * * 1-5'", text)
        self.assertIn("Full-Market 100-Stock Batches", text)


    def test_full_market_source_steps_receive_configured_krx_identity(self):
        """The KRX-MARCAP listing is queried in both plan and publish.

        Secret names must be bound at those steps, never interpolated into
        a command line or emitted to an Actions log.
        """
        text = Path(".github/workflows/full-market-batches.yml").read_text(
            encoding="utf-8")
        plan = text.split("      - id: plan\n", 1)[1].split("        shell:", 1)[0]
        combine = text.split(
            "      - name: Reconcile batch census; schedule publishes, manual audit is read-only\n",
            1)[1].split("        shell:", 1)[0]
        for step in (plan, combine):
            self.assertIn("KRX_ID: ${{ secrets.KRX_ID }}", step)
            self.assertIn("KRX_PW: ${{ secrets.KRX_PW }}", step)
            self.assertNotIn("echo $KRX", step)

if __name__ == "__main__":
    unittest.main()
