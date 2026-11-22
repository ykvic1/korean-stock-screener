"""Keep one-off authenticated KRX comparison audits opt-in.

Historical fixed-date batches must not be retriggered simply because code or
workflow YAML changes on main. Daily market-data update workflows are separate.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v3-historical-pilot-official-prices.yml"


class HistoricalOfficialWorkflowSafetyTest(unittest.TestCase):
    def test_fixed_historical_session_audit_is_manual_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("\non:\n  workflow_dispatch:\n", text)
        self.assertNotIn("\n  push:\n", text)
        self.assertNotIn("github.event_name == 'push'", text)
        self.assertNotIn("\n  schedule:\n", text)
        self.assertIn("downside_followup", text)
        self.assertIn("downside_tail", text)
        self.assertIn("reference_touches", text)
        self.assertIn("max-parallel: 1", text)
        self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
