"""Ensure the fixed 2025 five-firm research workflow is manual, bounded, and private."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/dart-five-firm-2025-ofs.yml'


class PinnedPilotWorkflowTests(unittest.TestCase):
    def test_manual_one_click_no_schedule_no_unrelated_provider(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('  workflow_dispatch:', content)
        self.assertNotIn('  schedule:', content)
        self.assertNotIn('  push:', content)
        self.assertNotIn('  pull_request:', content)
        self.assertNotIn('    inputs:', content)
        self.assertIn('  contents: read', content)
        self.assertIn('  cancel-in-progress: false', content)
        self.assertIn('    timeout-minutes: 20', content)
        self.assertIn('--provider dart --live', content)
        self.assertNotIn('--provider both', content)
        self.assertNotIn('KRX_API_KEY:', content)
        self.assertIn('retention-days: 7', content)
        self.assertNotIn('docs/data.json', content)
        self.assertNotIn('--finance-sample', content)
        self.assertNotIn('--history-sample', content)
        self.assertNotIn('--revision-audit', content)

    def test_all_five_verified_source_candidates_are_pinned_without_auto_scope_promotion(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        cases = (
            '252990:20260316001120:OFS',
            '280360:20260312000907:OFS',
            '018880:20260318000645:OFS',
            '014680:20260318001424:OFS',
            '064290:20260318001550:OFS',
        )
        for case in cases:
            self.assertEqual(content.count(case), 1)
        self.assertIn('--asof 2026-10-16', content)
        self.assertIn('--xbrl-batch-cases', content)
        self.assertIn('OFS is an explicit *research request*', content)
        self.assertIn('investment_scoring=false.', content)
        self.assertIn('accounting_scope_verified=false;', content)
        self.assertIn('if-no-files-found: warn', content)


if __name__ == '__main__':
    unittest.main()
