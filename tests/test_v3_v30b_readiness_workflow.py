"""Network-free constraints for the daily V3.0b release blocker watch."""
import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
WORKFLOW=ROOT/".github/workflows/v3-v30b-readiness-audit.yml"


class V30bReadinessWorkflowTests(unittest.TestCase):
    def test_daily_watch_is_read_only_and_independent(self):
        content=WORKFLOW.read_text(encoding="utf8")
        self.assertIn("cron: '23 0 * * *'",content)
        self.assertIn("contents: read",content)
        self.assertNotIn("contents: write",content)
        self.assertIn("workflow_dispatch:",content)
        self.assertIn("pull_request:",content)
        self.assertIn("GITHUB_STEP_SUMMARY",content)
        self.assertIn("if: always()",content)
        for forbidden in ("pip install","curl ","DART_API_KEY","KRX_API_KEY",
                          "git push","git commit","confirmed_score =",
                          "v30b_release_ready = true"):
            self.assertNotIn(forbidden,content)

    def test_main_audit_must_fail_closed_and_no_fake_release(self):
        content=WORKFLOW.read_text(encoding="utf8")
        self.assertIn("python v3_v30b_release_gate.py --assert-blocked",content)
        self.assertIn('if: ${{ github.event_name != \'pull_request\' }}',content)
        self.assertIn('set -euo pipefail',content)
        self.assertIn('v30b-readiness-aggregate.json',content)
        for required in (
            "public_summary_valid","public_issuer_csv_reconciled",
            "public_fcf_issuer_subscores_reconciled",
            "dart_historical_receipts_remaining_months",
            "historical_fixed_window_extracted","v30b_release_ready",
            "confirmed_scoring_authorized","investment_grade_certified_count",
            "blocker_codes","external_provider_calls",
        ):
            self.assertIn(required,content)
        self.assertNotIn("|| true",next(line for line in content.splitlines()
                                      if line.strip().startswith("python v3_v30b_release_gate.py")))


if __name__=="__main__":
    unittest.main()
