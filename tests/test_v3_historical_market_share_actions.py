"""Replay actions only while the holding has a verified chain."""

import json
import unittest

from v3_historical_market_coverage import END, ENTRY, SIGNAL, day
from v3_historical_market_share_actions import CALENDAR_CODES, continuity_actions, replay
from v3_historical_performance_pilot_audit import PilotAuditError


def quote(shares, price):
    return ("KOSPI", "company", shares, price, price, price, price, 50)


def split(new, old):
    return {
        "market": "KOSPI", "shares_before": None, "shares_after": None,
        "new_units": new, "old_units": old, "action": "split", "notice_url": "test",
    }


class HistoricalShareActionTests(unittest.TestCase):
    def test_paid_rights_eligibility_uses_open_entry_on_ex_date(self):
        early_listing, later_ex, later_listing = map(day, ("2026-01-13", "2026-03-18", "2026-05-15"))
        sessions = (SIGNAL, ENTRY, day("2025-12-15"), early_listing, day("2026-02-13"),
                    later_ex, day("2026-05-13"), later_listing, END)
        detached, entitled = "000001", "000002"
        panels = {s: dict.fromkeys((*CALENDAR_CODES, detached, entitled), quote(100, 100)) for s in sessions}
        for s in sessions[3:]:
            panels[s][detached] = quote(120, 100)
        for s in sessions[7:]:
            panels[s][entitled] = quote(120, 90)
        prefix = "https://kind.krx.co.kr/external/2025/11/13/000001/20251113000001/"
        terms = dict(code=detached, action="paid_rights_issue", market="KOSPI", name_before="company",
                     name_after="company", shares_before=100, shares_after=120, ex_on=str(ENTRY),
                     listed_on=str(early_listing), notice_url=prefix + "68154.htm",
                     rights_url=prefix + "99301.htm", terms_url=prefix + "11306.htm")
        second = dict(terms, code=entitled, ex_on=str(later_ex), listed_on=str(later_listing))
        report = replay(panels, set(), continuity_actions(json.dumps([terms, second]).encode()))
        holding, = report["horizons"]["12"]["rows"]
        self.assertEqual((holding["code"], holding["tradable_units_per_entry_share"], holding["price_change_pct"]),
                         (detached, "1", 0))
        withheld = report["unpriced_paid_rights_codes_by_horizon"]
        self.assertEqual(withheld["3"], [])
        self.assertEqual((withheld["6"], withheld["12"]), ([entitled], [entitled]))

    def test_bonus_path_accepts_a_paid_listing_only_for_already_detached_rights(self):
        bonus_ex, paid_on, bonus_on = map(day, ("2025-11-20", "2025-11-28", "2025-12-11"))
        sessions = (SIGNAL, ENTRY, bonus_ex, paid_on, bonus_on, day("2025-12-15"),
                    day("2026-01-13"), day("2026-02-13"), day("2026-05-13"), END)
        code = "000001"
        panels = {d: dict.fromkeys((*CALENDAR_CODES, code), quote(100, 120)) for d in sessions}
        panels[bonus_ex][code] = quote(100, 80)
        panels[paid_on][code] = quote(120, 80)
        for d in sessions[4:]:
            panels[d][code] = quote(180, 80)
        prefix = "https://kind.krx.co.kr/external/2025/11/13/000001/20251113000001/"
        bonus = dict(code=code, action="bonus_issue", market="KOSPI", name_before="company",
                     name_after="company", shares_before=120, shares_after=180, ex_on=str(bonus_ex),
                     paid_listing_on=str(paid_on), listed_on=str(bonus_on), eligible_shares=120,
                     ex_reference_krw=80, new_units=3, old_units=2,
                     notice_url=prefix + "70791.htm", rights_url=prefix + "70766.htm",
                     terms_url=prefix + "11308.htm")
        paid = dict(code=code, action="paid_rights_issue", market="KOSPI", name_before="company",
                    name_after="company", shares_before=100, shares_after=120, ex_on=str(SIGNAL),
                    listed_on=str(paid_on), notice_url=prefix + "68154.htm", rights_url=prefix + "99301.htm",
                    terms_url=prefix + "11306.htm")
        notices = continuity_actions(json.dumps([bonus, paid]).encode())
        holding, = replay(panels, set(), notices)["horizons"]["12"]["rows"]
        self.assertEqual((holding["price_change_pct"], holding["tradable_units_per_entry_share"]), (0, "3/2"))
        with self.assertRaisesRegex(PilotAuditError, "invalid_bonus_intervening_paid_listing"):
            continuity_actions(json.dumps([bonus, {**paid, "ex_on": "2025-11-19"}]).encode())
        panels[paid_on][code] = quote(121, 80)
        self.assertEqual(replay(panels, set(), notices)["horizons"]["12"]["rows"], [])

    def test_spin_off_marks_both_securities_and_leaves_intraday_adverse_unknown(self):
        halt = day("2026-07-30")
        listed = day("2026-08-25")
        sessions = (SIGNAL, ENTRY, day("2025-12-15"), day("2026-01-13"), day("2026-02-13"),
                    day("2026-05-13"), halt, listed, END)
        parent, child = "000001", "0220W0"
        panels = {d: dict.fromkeys((*CALENDAR_CODES, parent), quote(100, 100)) for d in sessions}
        panels[halt][parent] = ("KOSPI", "company", 100, 0, 0, 0, 100, 0)
        for d in (listed, END):
            panels[d][parent] = quote(75, 120)
            panels[d][child] = ("KOSPI", "child", *quote(150, 20)[2:])
        source = "https://kind.krx.co.kr/external/2026/08/21/000001/20260821000001/"
        notice = dict(code=parent, action="spin_off", market="KOSPI", name_before="company",
                      name_after="company", shares_before=100, shares_after=75,
                      new_units=3, old_units=4, child_code=child, child_name="child", child_shares=150,
                      child_new_units=3, child_old_units=2, halt_from=str(halt), listed_on=str(listed),
                      notice_url=source + "68155.htm", child_listing_url=source + "68156.htm",
                      terms_url=source + "00660.htm", halt_url=source + "68054.htm")
        parsed = continuity_actions(json.dumps([notice]).encode())
        result = replay(panels, set(), parsed)
        holding, = result["horizons"]["12"]["rows"]
        self.assertEqual(holding["price_change_pct"], 20)
        self.assertEqual(holding["tradable_units_per_entry_share"], "3/4")
        self.assertEqual(holding["child_units_per_entry_share"], "3/2")
        self.assertEqual(holding["unpriced_sessions"], 1)
        self.assertIsNone(holding["observed_adverse_excursion_pct"])
        self.assertEqual(holding["valuation_basis"], "proportional_spin_off_close")

        for mismatch in ({"new_units": 2}, {"child_code": parent}, {"child_new_units": True},
                         {"child_listing_url": notice["notice_url"]}):
            with self.subTest(mismatch=mismatch), self.assertRaisesRegex(PilotAuditError, "invalid_spin_off_terms"):
                continuity_actions(json.dumps([{**notice, **mismatch}]).encode())
        for date, code, invalid in (
            (halt, parent, quote(100, 100)),
            (halt, child, panels[listed][child]),
            (listed, child, ("KOSDAQ", "child", *quote(150, 20)[2:])),
            (END, child, ("KOSPI", "child", *quote(151, 20)[2:])),
        ):
            with self.subTest(date=date, code=code):
                damaged = {**panels, date: {**panels[date], code: invalid}}
                self.assertEqual(replay(damaged, set(), parsed)["horizons"]["12"]["rows"], [])

    def test_treasury_cancellation_during_halt_preserves_gaps_with_a_matched_resumption(self):
        halted_from = day("2025-12-12")
        cancellation = day("2025-12-15")
        halted_through = day("2026-01-13")
        resumed = day("2026-02-13")
        sessions = (SIGNAL, ENTRY, halted_from, cancellation, halted_through, resumed,
                    day("2026-05-13"), END)
        code = "000001"
        panels = {session: dict.fromkeys((*CALENDAR_CODES, code), quote(100, 100)) for session in sessions}
        suspended_quote = ("KOSPI", "company", 75, 0, 0, 0, 100, 0)
        panels[halted_from][code] = ("KOSPI", "company", 100, 0, 0, 0, 100, 0)
        for session in (cancellation, halted_through):
            panels[session][code] = suspended_quote
        for session in sessions[5:]:
            panels[session][code] = quote(75, 90)
        prefix = "https://kind.krx.co.kr/external/2025/12/12/000001/20251212000001/"
        treasury = dict(code=code, market="KOSPI", name_before="company", name_after="company",
                        action="treasury_cancellation", shares_before=100, shares_after=75,
                        notice_url=prefix + "70763.htm", terms_url=prefix + "71131.htm",
                        listed_on=str(cancellation), resumed_on=str(resumed), split_shares=750,
                        reduction_shares=675, split_url=prefix + "70764.htm",
                        reduction_url=prefix + "70650.htm", resume_url=prefix + "70799.htm")
        notices = continuity_actions(json.dumps([treasury]).encode())
        by_horizon = replay(panels, set(), notices)["horizons"]
        self.assertEqual(by_horizon["1"]["rows"], [])
        self.assertEqual(by_horizon["2"]["rows"], [])
        holding, = by_horizon["12"]["rows"]
        self.assertEqual(holding["unpriced_sessions"], 3)
        self.assertEqual(holding["price_change_pct"], -10)
        self.assertEqual(holding["observed_adverse_excursion_pct"], 10)
        self.assertEqual(holding["tradable_units_per_entry_share"], "1")

        with self.assertRaisesRegex(PilotAuditError, "invalid_suspended_treasury_chain"):
            continuity_actions(json.dumps([{**treasury, "reduction_shares": 674}]).encode())
        for session, invalid in ((halted_through, quote(75, 90)), (resumed, quote(76, 90)),
                                 (resumed, suspended_quote)):
            with self.subTest(session=session, quote=invalid):
                original = panels[session][code]
                panels[session][code] = invalid
                self.assertEqual(replay(panels, set(), notices)["horizons"]["12"]["rows"], [])
                panels[session][code] = original

    def test_treasury_cancellation_then_reverse_split_uses_ratio_after_cancellation(self):
        halt = day("2026-02-13")
        resumed = day("2026-05-13")
        sessions = (SIGNAL, ENTRY, day("2025-12-15"), day("2026-01-13"), halt,
                    day("2026-04-01"), resumed, END)
        code = "000001"
        panels = {session: dict.fromkeys((*CALENDAR_CODES, code), quote(100, 100)) for session in sessions}
        suspended = ("KOSPI", "company", 100, 0, 0, 0, 100, 0)
        for session in sessions[4:6]:
            panels[session][code] = suspended
        for session in sessions[6:]:
            panels[session][code] = quote(16, 450)
        prefix = "https://kind.krx.co.kr/external/2025/12/12/000001/20251212000001/"
        notice = dict(code=code, market="KOSPI", name_before="company", name_after="company",
                      action="treasury_cancellation_then_reverse_split", shares_before=100,
                      shares_after=16, cancelled_shares=20, old_units=5, listed_on=str(resumed),
                      halt_from=str(halt), notice_url=prefix + "70763.htm",
                      terms_url=prefix + "71131.htm", reverse_split_url=prefix + "70764.htm",
                      halt_url=prefix + "70797.htm", resume_url=prefix + "70799.htm")
        actions = continuity_actions(json.dumps([notice]).encode())
        horizons = replay(panels, set(), actions)["horizons"]
        self.assertEqual(horizons["3"]["rows"], [])
        holding, = horizons["12"]["rows"]
        self.assertEqual(holding["price_change_pct"], -10)
        self.assertEqual(holding["observed_adverse_excursion_pct"], 10)
        self.assertEqual(holding["tradable_units_per_entry_share"], "1/5")
        self.assertEqual(holding["unpriced_sessions"], 2)

        for mismatch in ({"cancelled_shares": 19}, {"old_units": 6}, {"halt_url": notice["resume_url"]}):
            with self.subTest(mismatch=mismatch), self.assertRaisesRegex(
                    PilotAuditError, "invalid_combined_cancellation_chain"):
                continuity_actions(json.dumps([{**notice, **mismatch}]).encode())
        for session, invalid in ((halt, None), (halt, quote(100, 100)), (resumed, quote(15, 450))):
            with self.subTest(session=session, quote=invalid):
                original = panels[session][code]
                if invalid is None:
                    del panels[session][code]
                else:
                    panels[session][code] = invalid
                self.assertEqual(replay(panels, set(), actions)["horizons"]["12"]["rows"], [])
                panels[session][code] = original

    def test_treasury_cancellation_mirrors_require_separate_terms_and_preserve_units(self):
        december = day("2025-12-15")
        sessions = (SIGNAL, ENTRY, december, day("2026-01-13"), day("2026-02-13"),
                    day("2026-05-13"), END)
        panels = {s: dict.fromkeys((*CALENDAR_CODES, "000001"), quote(100, 100)) for s in sessions}
        for session in sessions[2:]:
            panels[session]["000001"] = quote(75, 90)
        terms = dict(
            code="000001", market="KOSPI", name_before="company", name_after="company",
            action="treasury_cancellation", shares_before=100, shares_after=75,
            notice_url="https://spn.stockplus.com/news/api/v1/disclosure_views/koscom/123",
            terms_url="https://samara.kr/disclosure/20251202900001", listed_on=str(december),
        )
        notices = continuity_actions(json.dumps([terms]).encode())
        holding, = replay(panels, set(), notices)["horizons"]["12"]["rows"]
        self.assertEqual(holding["tradable_units_per_entry_share"], "1")
        self.assertEqual(holding["price_change_pct"], -10)
        self.assertEqual(holding["observed_adverse_excursion_pct"], 10)

        for bad_url in (terms["notice_url"], "https://example.com/terms"):
            with self.assertRaisesRegex(PilotAuditError, "missing_treasury_cancellation_terms"):
                continuity_actions(json.dumps([{**terms, "terms_url": bad_url}]).encode())
        with self.assertRaisesRegex(PilotAuditError, "invalid_continuity_notice"):
            continuity_actions(json.dumps([{**terms, "action": "bond_conversion"}]).encode())

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

    def test_bond_warrant_exercise_keeps_common_holder_units(self):
        listing = day("2026-01-13")
        sessions = (SIGNAL, ENTRY, day("2025-12-15"), listing,
                    day("2026-02-13"), day("2026-05-13"), END)
        code = "317870"
        panels = {s: dict.fromkeys((*CALENDAR_CODES, code), quote(100, 100)) for s in sessions}
        for session in sessions[3:]:
            panels[session][code] = quote(125, 95)
        listing_notice = "https://kind.krx.co.kr/external/2026/01/09/000001/20260109000001/70791.htm"
        terms = dict(code=code, action="bond_warrant_exercise", market="KOSPI",
                     name_before="company", name_after="company", shares_before=100,
                     shares_after=125, listed_on=str(listing), notice_url=listing_notice)
        notices = continuity_actions(json.dumps([terms]).encode())
        report = replay(panels, set(), notices)
        holding, = report["horizons"]["12"]["rows"]
        self.assertEqual((holding["tradable_units_per_entry_share"], holding["price_change_pct"]),
                         ("1", -5))
        self.assertEqual(report["unpriced_paid_rights_codes_by_horizon"]["12"], [])
        panels[listing][code] = quote(126, 95)
        self.assertEqual(replay(panels, set(), notices)["horizons"]["12"]["rows"], [])

    def test_verified_offering_and_preferred_conversion_do_not_change_common_holdings(self):
        date = "2025-12-15"
        code = "000001"
        prefix = "https://kind.krx.co.kr/external/2025/12/12/000001/20251212000001/"
        terms = dict(code=code, action="public_offering", market="KOSPI", name_before="company",
                     name_after="company", shares_before=100, shares_after=150, listed_on=date,
                     notice_url=prefix + "70791.htm", terms_url=prefix + "10601.htm",
                     existing_holder_rights=False)
        for change in (terms, {**terms, "action": "preferred_to_common"}):
            rows = continuity_actions(json.dumps([change]).encode())
            self.assertEqual((rows[date, code]["new_units"], rows[date, code]["old_units"]), (1, 1))
        for update in ({"existing_holder_rights": True}, {"terms_url": terms["notice_url"]}):
            with self.subTest(update=update), self.assertRaisesRegex(PilotAuditError, "unreviewed_public_offering_terms"):
                continuity_actions(json.dumps([{**terms, **update}]).encode())
