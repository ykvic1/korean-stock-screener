"""Replay actions only while the holding has a verified chain."""

import json
import unittest

from v3_historical_market_coverage import END, ENTRY, SIGNAL, day
from v3_historical_market_share_actions import CALENDAR_CODES, continuity_actions, replay


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

    def test_bonus_rights_are_valued_once_and_only_listed_units_are_tradable(self):
        ex_on = day("2025-12-15")
        listed_on = day("2026-01-13")
        sessions = (SIGNAL, ENTRY, ex_on, listed_on, day("2026-02-13"), day("2026-05-13"), END)
        panels = {s: dict.fromkeys((*CALENDAR_CODES, "000001"), quote(100, 120)) for s in sessions}
        panels[ex_on]["000001"] = quote(100, 100)
        for s in sessions[3:]:
            panels[s]["000001"] = quote(120, 100)
        url = "https://kind.krx.co.kr/external/2025/12/12/000001/20251212000001/70766.htm"
        terms = dict(code="000001", market="KOSPI", name_before="company", name_after="company",
                     action="bonus_issue", shares_before=100, shares_after=120, eligible_shares=100,
                     notice_url=url, rights_url=url, terms_url=url, listed_on=str(listed_on),
                     ex_on=str(ex_on), new_units=6, old_units=5, ex_reference_krw=100)
        notices = continuity_actions(json.dumps([terms]).encode())
        adjusted = replay(panels, set(), notices)
        before_listing, = adjusted["horizons"]["1"]["rows"]
        self.assertEqual(before_listing["price_change_pct"], 0)
        self.assertEqual(before_listing["tradable_units_per_entry_share"], "1")
        self.assertTrue(before_listing["valuation_includes_unlisted_entitlement"])
        after_listing, = adjusted["horizons"]["2"]["rows"]
        self.assertEqual(after_listing["price_change_pct"], 0)
        self.assertEqual(after_listing["tradable_units_per_entry_share"], "6/5")
        self.assertFalse(after_listing["valuation_includes_unlisted_entitlement"])
        panels[listed_on]["000001"] = quote(121, 100)
        broken = replay(panels, set(), notices)
        self.assertEqual(broken["horizons"]["1"]["rows"], [])
        self.assertEqual(broken["horizons"]["12"]["rows"], [])

    def test_rename_and_placement_preserve_units_only_when_both_sides_match(self):
        renaming = day("2025-12-15")
        placement = day("2026-01-13")
        sessions = (SIGNAL, ENTRY, renaming, placement, day("2026-02-13"), day("2026-05-13"), END)
        code = "000001"
        panels = {s: dict.fromkeys((*CALENDAR_CODES, code), quote(100, 100)) for s in sessions}
        for s in sessions[2:]:
            shares = 100 if s == renaming else 150
            panels[s][code] = ("KOSPI", "new company", *quote(shares, 90)[2:])
        url = "https://kind.krx.co.kr/external/2025/12/12/000001/20251212000001/70765.htm"
        first = dict(code=code, market="KOSPI", name_before="company", name_after="new company",
                     action="rename", shares_before=100, shares_after=100,
                     notice_url=url, listed_on=str(renaming))
        second = dict(first, action="third_party_placement", shares_after=150,
                      name_before="new company", listed_on=str(placement))
        result = replay(panels, set(), continuity_actions(json.dumps([first, second]).encode()))
        holding, = result["horizons"]["12"]["rows"]
        self.assertEqual(holding["tradable_units_per_entry_share"], "1")
        self.assertEqual(holding["price_change_pct"], -10)
        second["shares_after"] = 151
        result = replay(panels, set(), continuity_actions(json.dumps([first, second]).encode()))
        self.assertEqual(result["horizons"]["12"]["rows"], [])
