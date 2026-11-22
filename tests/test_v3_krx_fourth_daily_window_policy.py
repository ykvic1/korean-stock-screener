"""Safety regression for the completed, bounded V3 KRX historical replay."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FiveDailyKrxReplayPolicyTest(unittest.TestCase):
    def test_five_kst_windows_and_single_serial_group(self):
        source = (ROOT / ".github/workflows/v3-krx-derived-history.yml").read_text(
            encoding="utf-8"
        )
        crons = re.findall(r"^\s+- cron: '([^']+)'", source, re.MULTILINE)
        # The fixed historical source has reached 490/490 windows. Do not
        # recreate its scheduled collector after collection is complete.
        progress = json.loads((ROOT / "docs/v3-krx-derived-backfill-progress.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(progress["five_session_cohorts_completed"], 490)
        self.assertEqual(progress["five_session_cohorts_remaining"], 0)
        self.assertEqual(crons, [])
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("group: v3-krx-private-derived-history", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("timeout-minutes: 360", source)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", source)
        self.assertNotIn("contents: write", source)

    def test_daily_max_krx_calls_remains_bounded(self):
        from v3_krx_derived_backfill import MAX_SOURCE_CALLS, MAX_WINDOWS_PER_RUN

        self.assertEqual(MAX_WINDOWS_PER_RUN, 16)
        self.assertEqual(MAX_SOURCE_CALLS, 160)
        self.assertEqual(MAX_SOURCE_CALLS, 160)  # manual bounded replay, not daily cron


if __name__ == "__main__":
    unittest.main()
