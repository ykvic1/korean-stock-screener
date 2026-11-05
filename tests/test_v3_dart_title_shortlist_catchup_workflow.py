"""Regression guard for no-provider DART title-shortlist backlog acceleration."""
from pathlib import Path
import unittest


WORKFLOW = Path(".github/workflows/v3-private-dart-krx-event-crosscheck.yml")


class TitleShortlistCatchupWorkflowTests(unittest.TestCase):
    def test_bounded_resumable_private_title_catchup(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python v3_dart_candidate_catchup.py", workflow)
        from v3_dart_candidate_catchup import PHASES, title
        self.assertIn(title.run, [phase[1] for phase in PHASES])
        self.assertIn("timeout-minutes: 20", workflow)
        # All segments must use the original single-writer checkpoint lock.
        self.assertIn("group: v3-dart-private-historical-event-receipts", workflow)
        self.assertIn("queue: max", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        # This backfill is a private derived-data audit, not a DART API caller.
        self.assertNotIn("DART_API_KEY:", workflow)
        self.assertNotIn("GITHUB_TOKEN:", workflow)

    def test_private_candidate_and_no_proof_contract_remains(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python v3_dart_candidate_catchup.py", workflow)
        from v3_dart_candidate_catchup import PHASES, adjacent, monthly
        self.assertIn(adjacent.run, [phase[1] for phase in PHASES])
        self.assertIn(monthly.run, [phase[1] for phase in PHASES])
        self.assertIn("v3_private_dart_krx_adjacent_month.py", workflow)
        self.assertIn("v3_private_dart_krx_filing_title_triage.py", workflow)


if __name__ == "__main__":
    unittest.main()
