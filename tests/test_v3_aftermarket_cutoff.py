"""Only session chronology: source authenticity is deliberately never inferred."""
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from v3_aftermarket_cutoff import (
    AftermarketCutoffError,
    main,
    target_policy,
)


KST = ZoneInfo("Asia/Seoul")


class PostMarketCutoffTests(unittest.TestCase):
    def test_volume_scope_transitions_are_explicit(self):
        examples = [
            ("2025-03-03", "pre_nxt_krx_only", "KRX_REGULAR_SESSION_VOLUME"),
            ("2025-03-04", "nxt_aftermarket_before_krx_aftermarket", "KRX_NXT_ALL_SESSION_VOLUME_TO_2000"),
            ("2026-09-13", "nxt_aftermarket_before_krx_aftermarket", "KRX_NXT_ALL_SESSION_VOLUME_TO_2000"),
            ("2026-09-14", "krx_nxt_and_krx_aftermarket", "KRX_NXT_ALL_SESSION_VOLUME_TO_2000"),
        ]
        now = datetime(2026, 11, 12, 9, 0, tzinfo=KST)
        for day, era, scope in examples:
            with self.subTest(day=day):
                policy = target_policy(day, now=now)
                self.assertEqual(policy["era"], era)
                self.assertEqual(policy["requested_price_scope"], "KRX_REGULAR_CLOSE_1530")
                self.assertEqual(policy["requested_volume_scope"], scope)
                self.assertTrue(policy["complete_session_by_clock"])
                self.assertFalse(policy["source_regular_close_verified"])
                self.assertFalse(policy["source_required_volume_scope_verified"])
                self.assertFalse(policy["complete_price_volume_source_certified"])

    def test_aftermarket_clock_does_not_certify_a_feed(self):
        before = target_policy(
            "2026-11-11", now=datetime(2026, 11, 11, 20, 59, tzinfo=KST))
        after = target_policy(
            "2026-11-11", now=datetime(2026, 11, 11, 21, 0, tzinfo=KST))
        self.assertFalse(before["complete_session_by_clock"])
        self.assertTrue(after["complete_session_by_clock"])
        self.assertFalse(after["complete_price_volume_source_certified"])

    def test_next_completed_close_manual_dispatch_requires_real_date_pinned_source(self):
        root = Path(__file__).resolve().parents[1]
        manual = (root / ".github/workflows/v3-manual-complete-market-dated-source.yml").read_text()
        collector = (root / ".github/workflows/full-market-batches.yml").read_text()
        merger = (root / "full_market_batch.py").read_text()
        self.assertIn("github.event_name == 'push' && '2026-11-12'",manual)
        self.assertIn("target_day - saved_day != timedelta(days=1)",manual)
        self.assertIn("target_day != datetime.now(KST).date()",manual)
        self.assertIn("target_day.weekday() > 4",manual)
        self.assertIn("manual_full_market_aftermarket_not_complete",manual)
        self.assertIn("manual_full_market_already_published",manual)
        self.assertIn("date_pinned_live_listing_required_downstream",manual)
        self.assertIn("full-market-batches.yml/dispatches",manual)
        self.assertIn("--date-pinned-roster",collector)
        self.assertIn("v3_aftermarket_cutoff.py --manifest",collector)
        self.assertIn("date_pinned_roster_price_day_mismatch",merger)
        before = target_policy("2026-11-12",
            now=datetime(2026,11,12,20,59,tzinfo=KST))
        after = target_policy("2026-11-12",
            now=datetime(2026,11,12,21,0,tzinfo=KST))
        self.assertFalse(before["complete_session_by_clock"])
        self.assertTrue(after["complete_session_by_clock"])
        self.assertFalse(after["source_regular_close_verified"])
        self.assertFalse(after["source_required_volume_scope_verified"])

    def test_bad_dates_and_naive_clock_are_rejected(self):
        cases = [("2026-11-13", datetime(2026, 11, 12, tzinfo=KST)),
                 ("2026-2-1", datetime(2026, 11, 12, tzinfo=KST)),
                 ("2026-11-11", datetime(2026, 11, 12))]
        for day, now in cases:
            with self.subTest(day=day, now=now):
                with self.assertRaises(AftermarketCutoffError):
                    target_policy(day, now=now)

    def test_cli_rejects_invalid_manifest_without_raw_price_data(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({"schema": "unexpected", "asof": "2026-11-11"}))
            self.assertEqual(main(["--manifest", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
