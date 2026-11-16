"""Replay actions only while the holding has a verified chain."""

import unittest

from v3_historical_market_coverage import END, ENTRY, SIGNAL, day
from v3_historical_market_share_actions import CALENDAR_CODES, replay


def quote(shares, price):
    return ("KOSPI", "company", shares, price, price, price, price, 50)


def split(new, old):
    return {
        "market": "KOSPI", "shares_before": None, "shares_after": None,
        "new_units": new, "old_units": old, "action": "split", "notice_url": "test",
    }


class HistoricalShareActionTests(unittest.TestCase):
    def test_entry_units_and_later_split_and_unverified_break(self):
        december = day("2025-12-15")
        january = day("2026-01-13")
        sessions = (SIGNAL, ENTRY, december, january, day("2026-02-13"), day("2026-05-13"), END)
        panels = {s: dict.fromkeys((*CALENDAR_CODES, "A", "B", "C"), quote(100, 100))
                  for s in sessions}
        for session in sessions[1:]:
            panels[session]["A"] = quote(200, 50)
        for session in sessions[2:]:
            panels[session]["A"] = quote(400, 30)
            panels[session]["B"] = quote(200, 40)
            panels[session]["C"] = quote(300, 40)
        for session in sessions[3:]:
            panels[session]["B"] = quote(300, 30)
            panels[session]["C"] = quote(600, 20)
        notices = {(ENTRY.isoformat(), "A"): split(2, 1),
                   (december.isoformat(), "A"): split(2, 1),
                   (december.isoformat(), "B"): split(2, 1),
                   (december.isoformat(), "C"): split(2, 1),
                   (january.isoformat(), "C"): split(2, 1)}
        report = replay(panels, set(), notices)
        self.assertEqual(report["first_share_count_changes"],
                         {"increase": 3, "decrease": 0, "missing": 0})
        first = report["horizons"]["1"]["rows"]
        self.assertEqual([(r["code"], r["price_change_pct"]) for r in first], [("A", 20.0), ("B", -20.0)])
        self.assertEqual(first[-1]["observed_adverse_excursion_pct"], 20.0)
        self.assertEqual([r["code"] for r in report["horizons"]["12"]["rows"]], ["A"])
        unmatched_path = [r for r in report["matched_events"] if r["code"] == "C"]
        self.assertEqual(len(unmatched_path), 1)
        self.assertFalse(unmatched_path[0]["incorporated_into_path"])
