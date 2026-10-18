"""Synthetic IAS observation tests: no public raw flows or API calls."""
from datetime import date, timedelta
import copy
import unittest

from v3_ias_flow_ledger import (
    FlowLedgerError, PERIODS, PERIOD_WEIGHTS, SESSION, audit_daily_flow
)


def sample(length=250):
    d = date(2024, 1, 2)
    rows = []
    while len(rows) < length:
        if d.weekday() < 5:
            rows.append({
                "date": d.isoformat(), "session": SESSION, "market": "KOSPI",
                "foreign_net_shares": 10,
                "institution_total_net_shares": 20,
                "pension_net_shares": 5,
            })
        d += timedelta(days=1)
    return {
        "code": "000001", "issuer_id": "00000001", "market": "KOSPI",
        "institution_definition": "total_includes_pension",
        "pension_definition": "pension_etc_subcategory",
        "observations": rows,
    }


def analyze(ledger):
    return audit_daily_flow(ledger, asof=ledger["observations"][-1]["date"])


class IASFlowLedgerTests(unittest.TestCase):
    def test_period_weights_are_nested_without_double_count(self):
        x = analyze(sample())
        self.assertEqual(PERIODS, (20, 60, 120, 250))
        self.assertEqual(PERIOD_WEIGHTS, {20: 15, 60: 25, 120: 30, 250: 30})
        self.assertEqual(x["net_buy_period_weights_sum"], 100)
        self.assertTrue(x["pension_is_subcategory_of_institution"])
        self.assertTrue(x["never_double_add_pension_to_institution"])
        self.assertTrue(x["weights_not_applied_to_unscaled_net_shares"])
        for n in PERIODS:
            period = x["periods"][str(n)]
            self.assertEqual(period["categories"]["foreign_net_shares"]["cumulative_net_shares"], 10*n)
            self.assertEqual(period["categories"]["institution_total_net_shares"]["cumulative_net_shares"], 20*n)
            self.assertEqual(period["categories"]["pension_net_shares"]["cumulative_net_shares"], 5*n)
            self.assertIsNone(period["foreign_plus_institution_plus_pension_total"])
            self.assertFalse(period["independently_authenticated_calendar"])
        self.assertIsNone(x["ias_100_points"])
        self.assertIsNone(x["accumulated_net_buy_normalized_ias_points"])
        self.assertIsNone(x["v3_six_pillar_confirmed_points"])
        self.assertFalse(x["investor_flow_scoring_allowed"])
        self.assertFalse(x["confirmed_buy"])

    def test_actual_zero_is_not_missing_but_unknown_is_null(self):
        ledger = sample()
        ledger["observations"][-5]["institution_total_net_shares"] = 0
        x = analyze(ledger)
        self.assertEqual(x["periods"]["20"]["categories"]["institution_total_net_shares"]["cumulative_net_shares"], 20*19)
        ledger["observations"][-4]["institution_total_net_shares"] = None
        x = analyze(ledger)
        for n in PERIODS:
            self.assertFalse(x["periods"][str(n)]["categories"]["institution_total_net_shares"]["data_complete"])
            self.assertIsNone(x["periods"][str(n)]["categories"]["institution_total_net_shares"]["cumulative_net_shares"])
            self.assertEqual(x["periods"][str(n)]["categories"]["foreign_net_shares"]["cumulative_net_shares"], 10*n)

    def test_missing_long_windows_never_backfill_with_short_window(self):
        ledger = sample(20)
        x = analyze(ledger)
        self.assertEqual(x["periods"]["20"]["categories"]["foreign_net_shares"]["cumulative_net_shares"], 200)
        for n in (60, 120, 250):
            self.assertIsNone(x["periods"][str(n)]["categories"]["foreign_net_shares"]["cumulative_net_shares"])
            self.assertFalse(x["periods"][str(n)]["categories"]["foreign_net_shares"]["data_complete"])
        self.assertEqual(x["periods"]["250"]["observed_dates_count"], 0)

    def test_negative_net_flow_remains_negative_not_abs_buy(self):
        ledger = sample(20)
        ledger["observations"][-1]["foreign_net_shares"] = -210
        x = analyze(ledger)
        self.assertEqual(x["periods"]["20"]["categories"]["foreign_net_shares"]["cumulative_net_shares"], -20)
        self.assertIsNone(x["forecast_from_flow"])

    def test_claimed_source_pit_and_calendar_do_not_promote_scoring(self):
        ledger = sample()
        ledger.update({"official_source_authenticated": True,
                       "calendar_independently_verified": True,
                       "confirmed_buy": True, "ias_100_points": 100})
        x = analyze(ledger)
        self.assertFalse(x["all_rows_issuer_identity_authenticated"])
        self.assertFalse(x["official_session_and_calendar_verified"])
        self.assertFalse(x["category_taxonomy_independently_verified"])
        self.assertFalse(x["shares_corporate_action_adjusted_and_verified"])
        self.assertIsNone(x["ias_100_points"])
        self.assertFalse(x["investor_flow_scoring_allowed"])
        self.assertFalse(x["confirmed_buy"])

    def test_evidence_changes_digest_even_if_aggregate_same(self):
        a = sample()
        out1 = analyze(a)
        b = copy.deepcopy(a)
        b["observations"][-1]["foreign_net_shares"] += 5
        b["observations"][-2]["foreign_net_shares"] -= 5
        out2 = analyze(b)
        self.assertEqual(out1["periods"]["20"]["categories"]["foreign_net_shares"]["cumulative_net_shares"],
                         out2["periods"]["20"]["categories"]["foreign_net_shares"]["cumulative_net_shares"])
        self.assertNotEqual(out1["input_digest_for_change_detection"],
                            out2["input_digest_for_change_detection"])

    def test_stale_snapshot_visible_but_not_called_up_to_date(self):
        ledger = sample(20)
        cutoff = ledger["observations"][-1]["date"]
        x = audit_daily_flow(ledger, asof=(date.fromisoformat(cutoff) +
                                             timedelta(days=5)).isoformat())
        self.assertTrue(x["stale_vs_asof"])
        self.assertFalse(x["investor_flow_scoring_allowed"])

    def test_duplicate_future_unsorted_invalid_values_and_taxonomy_block(self):
        modifications = {
            "duplicate": lambda x: x["observations"][1].update(
                date=x["observations"][0]["date"]),
            "reversed": lambda x: x["observations"].reverse(),
            "future_date": lambda x: x["observations"][-1].update(date="2028-01-01"),
            "wrong_market": lambda x: x["observations"][0].update(market="KOSDAQ"),
            "wrong_session": lambda x: x["observations"][0].update(session="AFTER_HOURS"),
            "float": lambda x: x["observations"][0].update(foreign_net_shares=5.5),
            "bool": lambda x: x["observations"][0].update(foreign_net_shares=True),
            "overflow": lambda x: x["observations"][0].update(foreign_net_shares=10**14),
            "missing": lambda x: x["observations"][0].pop("pension_net_shares"),
            "wrong_pension_category": lambda x: x.update(pension_definition="separate_institution"),
            "wrong_institution": lambda x: x.update(institution_definition="foreign_plus_institution"),
            "bad_code": lambda x: x.update(code="00001X"),
            "bad_issuer": lambda x: x.update(issuer_id=""),
        }
        for name, mutate in modifications.items():
            with self.subTest(name=name):
                ledger = sample(25)
                cutoff = ledger["observations"][-1]["date"]
                mutate(ledger)
                with self.assertRaises(FlowLedgerError):
                    audit_daily_flow(ledger, asof=cutoff)


if __name__ == "__main__":
    unittest.main()
