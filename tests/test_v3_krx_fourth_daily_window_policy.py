"""Safety regression for bounded five-window V3 KRX historical replay."""

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
        self.assertEqual(
            crons,
            ["11 17 * * *", "11 22 * * *", "11 2 * * *", "11 5 * * *",
             "11 13 * * *"],
        )
        self.assertIn("group: v3-krx-private-derived-history", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("timeout-minutes: 55", source)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", source)
        self.assertNotIn("contents: write", source)

    def test_daily_max_krx_calls_remains_bounded(self):
        from v3_krx_derived_backfill import MAX_SOURCE_CALLS, MAX_WINDOWS_PER_RUN

        self.assertEqual(MAX_WINDOWS_PER_RUN, 16)
        self.assertEqual(MAX_SOURCE_CALLS, 160)
        self.assertEqual(5 * MAX_SOURCE_CALLS, 800)


if __name__ == "__main__":
    unittest.main()
