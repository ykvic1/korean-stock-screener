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

    def test_source_audit_preserves_verified_overnight_schedule(self):
        full_market = Path(".github/workflows/full-market-batches.yml")
        text = full_market.read_text(encoding="utf-8")
        self.assertIn("cron: '30 17 * * 1-5'", text)
        self.assertIn("--overnight-previous-day", text)
        self.assertIn("github.event_name", text)
        self.assertIn("Full-Market 100-Stock Batches", text)
        self.assertNotIn("cron: '30 12 * * 1-5'", text)


    def test_date_pinned_listing_needs_no_secret_and_flow_secrets_remain_optional(self):
        """The overnight previous-day price-universe roster must not depend on KRX login.

        Public cache is selected by the exact market asof in BOTH plan and
        publish. Separate investor-flow research may still accept secrets;
        no credentials may be interpolated into shell commands or logs.
        """
        text = Path(".github/workflows/full-market-batches.yml").read_text(
            encoding="utf-8")
        plan = text.split("      - id: plan\n", 1)[1].split("      - uses: actions/upload-artifact@v4", 1)[0]
        combine = text.split(
            "      - name: Reconcile batch census; schedule publishes, manual audit is read-only\n",
            1)[1].split("        shell:", 1)[0]
        self.assertIn("python full_market_batch.py plan --date-pinned-roster", plan)
        self.assertNotIn("KRX_ID: ${{ secrets.KRX_ID }}", plan)
        self.assertNotIn("KRX_PW: ${{ secrets.KRX_PW }}", plan)
        for secret in ("KRX_ID", "KRX_PW"):
            self.assertIn(f"{secret}: ${{{{ secrets.{secret} }}}}", combine)
        self.assertIn("roster_source_sha256",
                      Path("full_market_batch.py").read_text(encoding="utf-8"))
        self.assertNotIn("echo $KRX", plan + combine)

if __name__ == "__main__":
    unittest.main()
