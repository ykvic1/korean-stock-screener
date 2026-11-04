"""DART receipt catch-up is hourly, bounded, private and stops at 120 months."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DartFastLaneContractTests(unittest.TestCase):
    def test_offline_checks_cannot_wait_for_or_occupy_the_ingestion_queue(self):
        for filename, live_name in (
            ("v3-dart-historical-event-receipts.yml", "historical-filing-receipts"),
            ("v3-dart-historical-direct-fastlane.yml", "additional-private-collection"),
        ):
            with self.subTest(workflow=filename):
                source = (ROOT / ".github/workflows" / filename).read_text()
                self.assertNotRegex(source, r"(?m)^concurrency:")
                jobs = dict(re.findall(
                    r"(?ms)^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
                    source.split("\njobs:\n", 1)[1]))
                offline, live = jobs["offline-safety"], jobs[live_name]
                self.assertNotIn("concurrency:", offline)
                self.assertNotIn("secrets.", offline)
                self.assertNotIn("ref: main", offline)
                self.assertIn("python -m unittest", offline)
                self.assertNotRegex(offline, r"(?m)^\s+python v3_dart_")
                self.assertIn("needs: offline-safety", live)
                self.assertRegex(live, r"(?m)^    if:")
                self.assertIn("inputs.mode == 'live'", live)
                self.assertIn("github.event_name == 'schedule'", live)
                self.assertIn("group: v3-dart-private-historical-event-receipts", live)
                self.assertIn("cancel-in-progress: false", live)
                self.assertIn("queue: max", live)
                self.assertIn("persist-credentials: false", live)
                self.assertIn("secrets.DART_API_KEY", live)

    def test_hourly_collection_keeps_shared_lock_and_private_stop_gates(self):
        path=ROOT / ".github/workflows/v3-dart-historical-direct-fastlane.yml"
        source=path.read_text(encoding="utf8")
        collector=(ROOT / "v3_dart_historical_event_receipts.py").read_text(encoding="utf8")
        self.assertIn("cron: '31 * * * *'",source)
        self.assertIn("group: v3-dart-private-historical-event-receipts",source)
        self.assertIn("cancel-in-progress: false",source)
        self.assertIn("public_progress_complete",source)
        self.assertIn("steps.completion.outputs.done == 'false'",source)
        self.assertIn("V3_PRIVATE_ARCHIVE_REPO:",source)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:",source)
        self.assertIn("DART_API_KEY:",source)
        self.assertNotIn("permissions:\n  contents: write",source)
        self.assertIn("MAX_API_CALLS = 72",collector)
        self.assertIn("def public_progress_complete(",collector)
        self.assertIn("LAST = (2026, 9)",collector)

if __name__ == "__main__":
    unittest.main()
