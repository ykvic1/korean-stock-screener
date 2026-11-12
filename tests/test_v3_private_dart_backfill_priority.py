"""Keep auxiliary DART research from starving the 120-month receipt collector.

No vendor calls: validate only workflow source, trigger scope, shared lock and
the verified 02:30 KST next-day full-market schedule.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = (
    (".github/workflows/v3-private-dart-original-evidence.yml", 2, True),
    (".github/workflows/v3-dart-structured-actions-private.yml", 1, False),
    (".github/workflows/v3-dart-structured-action-extension.yml", 1, True),
)


class PrivateDartBackfillPriorityTests(unittest.TestCase):
    def test_research_workflows_are_bounded_scheduled_or_explicit_live_only(self):
        for path, schedule_count, shares_archive_lock in RESEARCH:
            with self.subTest(path=path):
                workflow = (ROOT / path).read_text(encoding="utf-8")
                self.assertEqual(len(re.findall(r"^    - cron:", workflow, re.M)),
                                 schedule_count)
                self.assertNotIn("  workflow_run:", workflow)
                self.assertIn("  workflow_dispatch:", workflow)
                self.assertIn("default: dry_run", workflow)
                self.assertIn("permissions:\n  contents: read", workflow)
                self.assertIn("cancel-in-progress: false", workflow)
                live_step = re.findall(
                    r"^        if: (.+)$", workflow, re.M)
                self.assertEqual(len(live_step), 1)
                self.assertIn("github.event_name == 'schedule'", live_step[0])
                self.assertIn("inputs.mode == 'live'", live_step[0])
                self.assertNotIn("github.event_name == 'push'", live_step[0])
                self.assertNotIn("github.event_name == 'workflow_run'",
                                 live_step[0])
                self.assertIn("persist-credentials: false", workflow)
                self.assertNotIn("git push", workflow)
                if shares_archive_lock:
                    self.assertIn(
                        "group: v3-dart-private-historical-event-receipts",
                        workflow)

    def test_historical_collectors_and_existing_market_schedule_remain_intact(self):
        regular = (ROOT / ".github/workflows/"
                   "v3-dart-historical-event-receipts.yml").read_text()
        fast = (ROOT / ".github/workflows/"
                "v3-dart-historical-direct-fastlane.yml").read_text()
        legacy = (ROOT / ".github/workflows/full-market-batches.yml").read_text()
        self.assertEqual(len(re.findall(r"^    - cron:", regular, re.M)), 12)
        self.assertIn("'31 * * * *'", fast)
        for workflow in (regular, fast):
            self.assertIn(
                "group: v3-dart-private-historical-event-receipts",
                workflow)
            self.assertIn("cancel-in-progress: false", workflow)
            self.assertIn("v3_dart_historical_event_receipts.py", workflow)
        self.assertIn("30 17 * * 1-5", legacy)
        self.assertIn("--overnight-previous-day", legacy)
        self.assertNotIn("30 12 * * 1-5", legacy)


if __name__ == "__main__":
    unittest.main()
