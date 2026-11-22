"""Proof boundaries for private official listed-share event anchor audit."""
import csv
from copy import deepcopy
from datetime import date, timedelta
from unittest import TestCase

import v3_historical_official_share_event_anchors as shares
from v3_historical_market_coverage import ROOT


class ReviewedShareAnchorTests(TestCase):
    def setUp(self):
        self.sessions = [date(2026, 11, 12), date(2026, 11, 13)]
        self.event = {
            "market": "KOSPI", "code": "000001", "name": "기업 A",
            "listing_on": "2026-11-13", "action": "split",
            "shares_before": "10", "shares_after": "20",
            "new_units": "2", "old_units": "1",
            "notice_url": "https://kind.krx.co.kr/external/test",
        }

    def test_exact_both_official_share_anchors_and_private_only_observations(self):
        plan, count = shares.plan_events([self.event], self.sessions)
        prior = plan[self.sessions[0]]
        listed = plan[self.sessions[1]]
        obs = shares.observation_rows(
            {"000001": ("KOSPI", "기업 A", 10, 10, 12, 9, 11, 10)},
            {"000001": ["KOSPI", "기업 A", 11, 10, None, True]},
            prior,
        )
        obs += shares.observation_rows(
            {"000001": ("KOSPI", "기업 A", 20, 5, 6, 4, 5, 10)},
            {"000001": ["KOSPI", "기업 A", 5, 20, None, True]},
            listed,
        )
        result = shares.summarize(count, obs)
        self.assertTrue(shares.valid_summary(result))
        self.assertEqual(result["reviewed_events"], 1)
        self.assertEqual(result["prior_anchor_exact_shares"], 1)
        self.assertEqual(result["listing_anchor_exact_shares"], 1)
        self.assertEqual(result["both_anchor_exact_shares"], 1)
        self.assertTrue(result["all_adjacent_share_anchors_match"])
        self.assertNotIn("000001", repr(result))
        self.assertNotIn("기업 A", repr(result))
        self.assertFalse(result["adjusted_price_history_verified"])
        self.assertFalse(result["performance_validated"])

    def test_partial_or_wrong_official_issuer_never_passes(self):
        plan, _ = shares.plan_events([self.event], self.sessions)
        listed = plan[self.sessions[1]][0]
        correct = {"000001": ("KOSPI", "기업 A", 20, 5, 6, 4, 5, 10)}
        for saved, expected in (
            ({}, "official_row_missing"),
            ({"000001": ["KOSPI", "기업 B", 5, 20, None, True]}, "identity_mismatch"),
            ({"000001": ["KOSDAQ", "기업 A", 5, 20, None, True]}, "identity_mismatch"),
            ({"000001": ["KOSPI", "기업 A", 5, 19, None, True]},
             "official_listed_shares_mismatch"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(shares.compare_anchor(correct, saved, listed), expected)
                report = shares.summarize(
                    1, [(0, "prior_session", "exact"),
                        (0, "listing_session", expected)])
                self.assertTrue(shares.valid_summary(report))
                self.assertFalse(report["all_adjacent_share_anchors_match"])

    def test_bad_ratio_duplicate_or_missing_calendar_is_rejected(self):
        for mutation in ("bad_ratio", "duplicate", "missing_listing", "fake_kind",
                         "missing_notice"):
            changed = deepcopy(self.event)
            if mutation == "bad_ratio":
                changed["shares_after"] = "21"
            elif mutation == "fake_kind":
                changed["action"] = "unreviewed"
            elif mutation == "missing_notice":
                changed["notice_url"] = "http://unverified.invalid"
            if mutation == "duplicate":
                records, sessions = [changed, deepcopy(changed)], self.sessions
            elif mutation == "missing_listing":
                records, sessions = [changed], [self.sessions[0], date(2026, 11, 14)]
            else:
                records, sessions = [changed], self.sessions
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                shares.plan_events(records, sessions)

    def test_public_summary_tampering_rejected(self):
        report = shares.summarize(2, [
            (0, "prior_session", "exact"),
            (0, "listing_session", "exact"),
            (1, "prior_session", "exact"),
            (1, "listing_session", "official_listed_shares_mismatch"),
        ])
        self.assertTrue(shares.valid_summary(report))
        self.assertEqual(report["both_anchor_exact_shares"], 1)
        for modify in ("extra_pair", "false_count", "fabricated_certification", "bad_stage"):
            r = deepcopy(report)
            if modify == "extra_pair":
                r["by_pair"]["exact|exact"] += 1
            elif modify == "false_count":
                r["both_anchor_exact_shares"] += 1
            elif modify == "fabricated_certification":
                r["adjusted_price_history_verified"] = True
            else:
                r["by_stage"]["listing_session"]["official_listed_shares_mismatch"] = 0
            with self.subTest(kind=modify):
                self.assertFalse(shares.valid_summary(r))

    def test_real_96_reviewed_events_ratio_reproduces_frozen_ledger(self):
        path = ROOT / "ops/v3-performance-reviewed-share-actions-20251113.csv"
        with path.open(encoding="utf-8", newline="") as f:
            events = list(csv.DictReader(f))
        all_days = [date(2025, 11, 13) + timedelta(days=n)
                    for n in range((date(2026, 11, 13) - date(2025, 11, 13)).days + 1)]
        plan, count = shares.plan_events(events, all_days)
        self.assertEqual(count, 96)
        self.assertEqual(sum(len(x) for x in plan.values()), 192)
        self.assertEqual(sum(e["action"] == "reverse_split" for e in events), 83)
        self.assertEqual(sum(e["action"] == "split" for e in events), 6)
        self.assertEqual(sum(e["action"] == "capital_reduction" for e in events), 7)
