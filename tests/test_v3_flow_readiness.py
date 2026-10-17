"""v3 flow candidate tests use offline fixtures and current published snapshot only."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import json
from pathlib import Path
import unittest

from v3_flow_readiness import (
    diagnose_v2_snapshot, examine_daily_flow_candidate, PERIODS,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture_snapshot():
    return {
        "status": "success", "date": "2026-10-16",
        "candidate_count": 120, "flow_confirmed_count": 0,
        "flow_attempted_count": 3, "error_count": 5,
        "collection_errors": [
            ["252990","기관·외국인 수급",
             'Npay: TimeoutError: Locator.wait_for: Timeout 20000ms exceeded. '
             'waiting for locator("[class*=TraderTrend_trader-trend-toolbar-exchange]")'],
            ["452260","기관·외국인 수급",
             'Npay: TimeoutError: Locator.wait_for: Timeout 20000ms exceeded. '
             'waiting for locator("[class*=TraderTrend_trader-trend-toolbar-exchange]")'],
            ["064290","기관·외국인 수급",
             'Npay: TimeoutError: Locator.wait_for: Timeout 20000ms exceeded. '
             'waiting for locator("[class*=TraderTrend_trader-trend-toolbar-exchange]")'],
            ["092870","기관·외국인 수급","deferred: consecutive supplier timeouts"],
            ["144960","기관·외국인 수급","deferred: consecutive supplier timeouts"],
        ],
    }


def flow_rows(count, *, asof="2026-10-16"):
    stop = date.fromisoformat(asof)
    return [
        {"code":"252990", "trade_date":(stop-timedelta(days=i)).isoformat(),
         "foreign_net_shares":i-5, "inst_net_shares":5-i}
        for i in range(count)
    ]


class V3FlowReadinessTests(unittest.TestCase):
    def test_root_supplier_failures_not_miscounted_as_independent_205_failures(self):
        report = diagnose_v2_snapshot(fixture_snapshot())
        self.assertEqual(report["actual_supplier_attempts_reported"],3)
        self.assertEqual(report["actual_observed_npay_exchange_locator_timeouts"],3)
        self.assertEqual(report["circuit_breaker_follow_on_deferrals"],2)
        self.assertEqual(report["confirmed_flow_candidates_in_existing_snapshot"],0)
        self.assertFalse(report["missing_flow_is_zero_net_shares"])
        self.assertIsNone(report["ias_investor_flow_component"])
        self.assertFalse(report["confirmed_scoring_allowed"])
        self.assertFalse(report["n_pay_dom_structure_independently_rechecked"])
        self.assertNotIn("Locator.wait_for",json.dumps(report))

    def test_actual_existing_daily_snapshot_flow_census(self):
        existing=json.loads((ROOT / "docs/data.json").read_text(encoding="utf-8"))
        report=diagnose_v2_snapshot(existing)
        self.assertEqual(report["asof"],existing["date"])
        self.assertEqual(report["flow_error_rows"],208)
        self.assertEqual(report["actual_supplier_attempts_reported"],3)
        self.assertEqual(report["actual_observed_npay_exchange_locator_timeouts"],3)
        self.assertEqual(report["circuit_breaker_follow_on_deferrals"],205)
        self.assertEqual(report["confirmed_flow_candidates_in_existing_snapshot"],0)
        self.assertFalse(report["verified_regular_session_investor_flows"])
        self.assertFalse(report["v3_release_ready"])

    def test_input_census_fails_closed_on_duplicate_malformed_or_bad_counts(self):
        for failure in ("duplicate","missing_code","bad_count","bad_attempts",
                        "success_overstated","too_large","wrong_date","not_success"):
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                snap=fixture_snapshot()
                if failure=="duplicate":
                    snap["collection_errors"][4][0]=snap["collection_errors"][3][0]
                elif failure=="missing_code":
                    snap["collection_errors"][3][0]="?"
                elif failure=="bad_count":
                    snap["error_count"]=4
                elif failure=="bad_attempts":
                    snap["flow_attempted_count"]=1
                elif failure=="success_overstated":
                    snap["flow_confirmed_count"]=121
                elif failure=="too_large":
                    snap["collection_errors"][0][2]="x"*2501
                elif failure=="wrong_date":
                    snap["date"]="2026-02-30"
                elif failure=="not_success":
                    snap["status"]="failed"
                diagnose_v2_snapshot(snap)

    def test_20_rows_observed_but_not_60_120_250_and_not_authorized(self):
        report=examine_daily_flow_candidate("252990","2026-10-16",flow_rows(20))
        self.assertEqual(report["observed_numeric_rows"],20)
        self.assertTrue(report["windows"]["20"]["numerical_window_complete"])
        self.assertEqual(report["windows"]["20"]["foreign_net_shares"],sum(i-5 for i in range(20)))
        for period in ("60","120","250"):
            self.assertFalse(report["windows"][period]["numerical_window_complete"])
            self.assertIsNone(report["windows"][period]["inst_net_shares"])
        self.assertFalse(report["independent_source_crosscheck_passed"])
        self.assertFalse(report["market_participant_identity_verified"])
        self.assertFalse(report["investor_flow_scoring_allowed"])

    def test_true_observed_zero_is_distinct_from_missing_null(self):
        rows=flow_rows(20)
        for v in rows:
            v["inst_net_shares"]=0
        report=examine_daily_flow_candidate("252990","2026-10-16",rows)
        self.assertEqual(report["windows"]["20"]["inst_net_shares"],0)
        self.assertEqual(report["windows"]["20"]["inst_positive_days"],0)
        del rows[0]["inst_net_shares"]
        with self.assertRaises(ValueError):
            examine_daily_flow_candidate("252990","2026-10-16",rows)
        rows[0]["inst_net_shares"]=None
        with self.assertRaises(ValueError):
            examine_daily_flow_candidate("252990","2026-10-16",rows)

    def test_full_250_rows_still_unverified_market_calendar_and_exchange(self):
        rows=flow_rows(250)
        r=examine_daily_flow_candidate("252990","2026-10-16",rows)
        self.assertEqual(tuple(PERIODS),(20,60,120,250))
        self.assertTrue(all(w["numerical_window_complete"] for w in r["windows"].values()))
        self.assertIsNone(r.get("ias_score"))
        self.assertFalse(r["trading_calendar_and_session_independently_verified"])
        self.assertFalse(r["confirmed_scoring_allowed"])

    def test_stale_rows_not_counted_as_current_window(self):
        r=examine_daily_flow_candidate("252990","2026-10-16",
                                       flow_rows(20,asof="2026-10-01"))
        self.assertFalse(r["freshness_observed"])
        self.assertFalse(r["windows"]["20"]["numerical_window_complete"])
        self.assertIsNone(r["windows"]["20"]["foreign_net_shares"])

    def test_invalid_daily_rows_never_promote_to_score(self):
        for failure in ("duplicate","future","missing_value","float","string",
                        "boolean","wrong_stock","fake_authority","huge",
                        "too_many_rows"):
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                rows=flow_rows(20)
                if failure=="duplicate":rows[1]["trade_date"]=rows[0]["trade_date"]
                elif failure=="future":rows[0]["trade_date"]="2026-10-17"
                elif failure=="missing_value":del rows[0]["foreign_net_shares"]
                elif failure=="float":rows[0]["foreign_net_shares"]=1.5
                elif failure=="string":rows[0]["foreign_net_shares"]="0"
                elif failure=="boolean":rows[0]["foreign_net_shares"]=True
                elif failure=="wrong_stock":rows[0]["code"]="005930"
                elif failure=="fake_authority":rows[0]["regular_session_verified"]=True
                elif failure=="huge":rows[0]["inst_net_shares"]=10**15
                elif failure=="too_many_rows":rows=flow_rows(251)
                examine_daily_flow_candidate("252990","2026-10-16",rows)

    def test_empty_observation_stays_unknown(self):
        r=examine_daily_flow_candidate("252990","2026-10-16",[])
        self.assertEqual(r["observed_numeric_rows"],0)
        self.assertIsNone(r["latest_row_date"])
        self.assertFalse(r["windows"]["20"]["numerical_window_complete"])
        self.assertIsNone(r["windows"]["20"]["foreign_net_shares"])
        self.assertFalse(r["investor_flow_scoring_allowed"])


if __name__=="__main__":
    unittest.main()
