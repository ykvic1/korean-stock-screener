"""KRX close + holder-unit quote proof is stricter than date-only price matching."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from unittest import TestCase

import v3_historical_official_holder_terminal as audit
import v3_historical_close_archive_audit as close_audit


class HistoricalOfficialHolderTerminalTests(TestCase):
    def test_17_historical_origins_plan_79_quoted_and_6_cash_or_unpriced_horizons(self):
        plan, rows, cash = audit.terminal_plan()
        self.assertEqual(rows, 85)
        self.assertEqual(cash, 6)
        self.assertEqual(sum(map(len, plan.values())), 79)
        self.assertEqual(len({x[0] for batch in plan.values() for x in batch}), 79)
        self.assertTrue(all(audit.ENTRY <= session <= audit.END for session in plan))
        self.assertTrue(all(event[2] > 0 and event[3] > 0 and event[4] > 0
                            for batch in plan.values() for event in batch))

    def test_original_or_successor_official_quote_and_holder_book_value_not_interchangeable(self):
        pub = {
            "000001": ("KOSPI", "Issuer", 100, 480, 520, 465, 490, 20),
            "000002": ("KOSDAQ", "Successor", 700, 100, 110, 90, 105, 20),
        }
        official = {
            "000001": ["KOSPI", "Issuer", 490, 100, None, True],
            "000002": ["KOSDAQ", "Successor", 105, 700, None, True],
        }
        observations = [
            (0, "000001", Decimal("490"), Decimal("490.0000"), Decimal(1)),
            (1, "000002", Decimal("105"), Decimal("42.0000"), Decimal("0.4")),
        ]
        matched = audit.observe(pub, official, observations)
        self.assertEqual(matched, [(0, "exact_official_holder_close"),
                                   (1, "exact_official_holder_close")])
        changes = []
        edited = deepcopy(official)
        edited["000002"][2] = 104
        changes.append((pub, edited, observations, "official_close_differs_from_public"))
        edited = deepcopy(official)
        edited["000002"][3] = 701
        changes.append((pub, edited, observations, "listed_shares_changed"))
        edited = deepcopy(official)
        edited["000002"][5] = False
        changes.append((pub, edited, observations, "official_quote_not_traded"))
        edited = deepcopy(official)
        edited["000002"][0] = "KOSPI"
        changes.append((pub, edited, observations, "wrong_issuer_or_market"))
        edited = deepcopy(observations)
        edited[1] = (*edited[1][:2], Decimal(104), *edited[1][3:])
        changes.append((pub, official, edited,
                        "quoted_close_differs_from_frozen_horizon"))
        edited = deepcopy(observations)
        edited[1] = (*edited[1][:3], Decimal("42.1000"), *edited[1][4:])
        changes.append((pub, official, edited,
                        "holder_value_differs_from_frozen_horizon"))
        for source, market, events, expected in changes:
            with self.subTest(reason=expected):
                self.assertEqual(audit.observe(source, market, events)[1][1], expected)

    def test_any_missing_quoted_horizon_or_unmatched_quote_cannot_pass_release_audit(self):
        full = audit.summarize(
            85, 6, [(i, "exact_official_holder_close") for i in range(79)])
        self.assertTrue(audit.valid_summary(full))
        self.assertTrue(full["all_quoted_horizons_have_exact_holder_close"])
        for observations in (
            [(i, "exact_official_holder_close") for i in range(78)],
            [(i, "exact_official_holder_close") for i in range(78)]
            + [(78, "quoted_close_differs_from_frozen_horizon")],
        ):
            report = audit.summarize(85, 6, observations)
            self.assertTrue(audit.valid_summary(report))
            self.assertFalse(report["all_quoted_horizons_have_exact_holder_close"])
            self.assertFalse(report["performance_validated"])
        with self.assertRaises(ValueError):
            audit.summarize(85, 6, [(1, "exact_official_holder_close")] * 2)
        tampered = deepcopy(full)
        tampered["complete_corporate_action_history_certified"] = True
        self.assertFalse(audit.valid_summary(tampered))
        tampered = deepcopy(full)
        tampered["outcomes"]["exact_official_holder_close"] -= 1
        self.assertFalse(audit.valid_summary(tampered))

    def test_existing_full_archive_audit_requires_holder_quotes_without_extra_api_calls(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/v3-historical-close-archive-audit.yml"
                    ).read_text(encoding="utf8")
        source = (Path(__file__).resolve().parents[1] /
                  "v3_historical_close_archive_audit.py").read_text(encoding="utf8")
        self.assertIn("v3_historical_official_holder_terminal.py", workflow)
        self.assertIn("test_v3_historical_official_holder_terminal.py", workflow)
        self.assertIn("holder_terminal_official_close", source)
        self.assertIn("if use_holder_terminal else None", source)
        self.assertIn("valid_holder_terminal", source)
        self.assertIn("all_quoted_horizons_have_exact_holder_close", source)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)


if __name__ == "__main__":
    import unittest
    unittest.main()
