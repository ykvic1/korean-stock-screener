"""Offline guard for bounded, timezone-correct rolling OpenDART collection slots.

This checks workflow scheduling only. It never contacts OpenDART or counts
unobserved issuer financials as success.
"""
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v3-rolling-dart-annual-book.yml"
COLLECTOR = ROOT / "v3_dart_rolling_book_reference.py"


class RollingDartWeekdayScheduleContract(unittest.TestCase):
    def test_korea_weekday_slots_with_utc_sunday_rollover(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        crons = re.findall(r"^\s+- cron: '([^']+)'", content, flags=re.M)
        # GitHub Actions cron runs in UTC. Sunday UTC 22:00 is Monday KST 07:00.
        self.assertEqual(crons, [
            "0 22 * * 0-4",  # M-F 07:00 Korea
            "0 4 * * 1-5",   # M-F 13:00 Korea
            "0 12 * * 1-5",  # M-F 21:00 Korea
        ])
        self.assertNotIn("cancel-in-progress: true", content)
        self.assertIn("group: korea-stock-screener-publication", content)
        self.assertIn("cancel-in-progress: false", content)
        self.assertIn("timeout-minutes: 24", content)

    def test_bounded_requests_and_fail_closed_publishing_unchanged(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        collector = COLLECTOR.read_text(encoding="utf-8")
        # This schedule does not increase per-execution provider requests.
        self.assertIn("MAX_PER_RUN=40", collector)
        self.assertIn("options: ['20', '40']", workflow)
        self.assertIn("quota=int(os.getenv('REQUESTED_LIMIT') or '40')", workflow)
        self.assertIn("if age not in range(0,10):", workflow)
        self.assertIn("raise ValueError('stale_dart_source_date')", workflow)
        self.assertIn("git pull --rebase origin main", workflow)
        self.assertIn("raise ValueError('post_rebase_book_census_changed')", workflow)
        self.assertIn("if result[k] is not False:", workflow)
        self.assertIn("docs/v3-dart-book-reference.json", workflow)


if __name__ == "__main__":
    unittest.main()
