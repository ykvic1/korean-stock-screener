"""Offline stock daily investor API contract regression. No API calls."""
from __future__ import annotations

import copy
import json
import unittest

import pandas as pd

from v3_kis_daily_investor_adapter import (
    DAILY_ENDPOINT, DAILY_TR_ID, MARKET,
    InvalidKisDailyInvestorEvidence, parse_kis_stock_daily,
    crosscheck_kis_stock_daily,
)


def example(*, rows=30, subgroups=True):
    days = [d.strftime("%Y%m%d")
            for d in pd.bdate_range(end="2026-10-16", periods=rows)]
    npay, official_rows = [], []
    for i, day in enumerate(reversed(days)):
        orgn = (rows-i)*71-1000
        foreign = 2500-(rows-i)*43
        official = {
            "stck_bsop_date": day,
            "orgn_ntby_qty": f"{orgn:+,d}",
            "frgn_ntby_qty": f"{foreign:+,d}",
        }
        if subgroups:
            official.update({
                "ivtr_ntby_qty": f"{(rows-i)*7:+,d}",
                "insu_ntby_qty": "-30",
                "fund_ntby_qty": "+0",
            })
        official_rows.append(official)
        if i < 20:
            npay.append({
                "itemCode": "252990", "bizdate": day,
                "organPureBuyQuant": official["orgn_ntby_qty"],
                "foreignerPureBuyQuant": official["frgn_ntby_qty"],
            })
    return npay, {"rt_cd": "0", "output1": [{"rprs_mrkt_kor_name": "KRX"}],
                  "output2": official_rows}


class KisStockDailyInvestorTests(unittest.TestCase):
    def test_distinct_official_stock_daily_endpoint_not_basic_price_endpoint(self):
        self.assertEqual(
            DAILY_ENDPOINT,
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily")
        self.assertEqual(DAILY_TR_ID, "FHPTJ04160001")
        self.assertEqual(MARKET, "J")

    def test_all_twenty_dates_match_research_only_even_with_subgroups(self):
        npay, document = example()
        parsed = parse_kis_stock_daily(document)
        self.assertEqual(len(parsed), 30)
        self.assertEqual(parsed[0]["trade_date"], "2026-10-16")
        self.assertEqual(parsed[0]["fund_general"], 0)
        audit = crosscheck_kis_stock_daily(npay, document)
        self.assertEqual(audit["status"],
                         "twenty_days_exact_numeric_agreement_research_only")
        self.assertEqual(audit["aligned_trading_days"], 20)
        self.assertEqual(audit["institutional_exact_match_days"], 20)
        self.assertEqual(audit["foreign_exact_match_days"], 20)
        self.assertEqual(audit["subgroup_coverage_rows"],
                         {"investment_trust": 30, "insurance": 30,
                          "fund_general": 30})
        for key in ("fund_general_equals_pension_verified",
                    "observed_60_120_250_day_windows_verified",
                    "kis_live_authentication_and_service_access_verified",
                    "kis_historical_source_retrieval_verified",
                    "kis_response_origin_authenticated",
                    "regular_session_and_category_definitions_verified",
                    "vendor_automated_reuse_permission_verified",
                    "confirmed_scoring_allowed", "v3_release_ready"):
            self.assertIs(audit[key], False, key)
        self.assertIsNone(audit["ias_flow_component"])
        report = json.dumps(audit)
        for not_permitted in ("ivtr_ntby_qty", "stck_bsop_date",
                              "orgn_ntby_qty", "+2,500", "-1,000"):
            self.assertNotIn(not_permitted, report)

    def test_missing_entire_optional_group_is_null_not_zero(self):
        npay, document = example(subgroups=False)
        rows = parse_kis_stock_daily(document)
        self.assertEqual(len(rows), 30)
        self.assertIsNone(rows[0]["fund_general"])
        audit = crosscheck_kis_stock_daily(npay, document)
        self.assertEqual(
            audit["subgroup_coverage_rows"],
            {"investment_trust": None, "insurance": None, "fund_general": None})
        self.assertTrue(audit["both_categories_exact_all_20"])
        self.assertFalse(audit["confirmed_scoring_allowed"])

    def test_one_day_mismatch_does_not_promote_research(self):
        npay, document = example()
        document["output2"][1]["orgn_ntby_qty"] = "+9999"
        report = crosscheck_kis_stock_daily(npay, document)
        self.assertEqual(report["status"], "investor_quantities_mismatch")
        self.assertEqual(report["institutional_exact_match_days"], 19)
        self.assertEqual(report["foreign_exact_match_days"], 20)
        self.assertFalse(report["confirmed_scoring_allowed"])

    def test_bad_day_alignment_is_not_a_numeric_agreement(self):
        npay, document = example()
        document["output2"][19]["stck_bsop_date"] = "20260901"
        report = crosscheck_kis_stock_daily(npay, document)
        self.assertEqual(report["status"], "trading_day_sets_differ")
        self.assertLess(report["aligned_trading_days"], 20)
        self.assertFalse(report["both_categories_exact_all_20"])

    def test_basic_inquire_investor_payload_cannot_pass_daily_contract(self):
        _, document = example()
        old = {"rt_cd": "0", "output": document["output2"]}
        with self.assertRaises(InvalidKisDailyInvestorEvidence):
            parse_kis_stock_daily(old)
        document["output"] = copy.deepcopy(document["output2"])
        with self.assertRaises(InvalidKisDailyInvestorEvidence):
            parse_kis_stock_daily(document)

    def test_invalid_identity_date_status_and_shape_are_rejected(self):
        for case in ("status", "wrong_code", "wrong_market", "wrong_query_day",
                     "invalid_asof", "short", "oversize", "wrong_container",
                     "wrong_rows", "duplicate", "future", "invalid_date",
                     "missing_required", "missing_required_value",
                     "noninteger", "decimal", "scientific", "bool"):
            _, document = example()
            args = {}
            if case == "status": document["rt_cd"] = "1"
            if case == "wrong_code": args["code"] = "005930"
            if case == "wrong_market": args["market"] = "NX"
            if case == "wrong_query_day": args["request_date"] = "20261015"
            if case == "invalid_asof": args["asof"] = "2026-02-30"
            if case == "short": document["output2"] = document["output2"][:19]
            if case == "oversize": document["output2"] *= 2
            if case == "wrong_container": document["output2"] = {}
            if case == "wrong_rows": document["output2"][0] = []
            if case == "duplicate":
                document["output2"][1]["stck_bsop_date"] = (
                    document["output2"][0]["stck_bsop_date"])
            if case == "future": document["output2"][0]["stck_bsop_date"] = "20261017"
            if case == "invalid_date":
                document["output2"][0]["stck_bsop_date"] = "20260230"
            if case == "missing_required":
                del document["output2"][0]["orgn_ntby_qty"]
            if case == "missing_required_value":
                document["output2"][0]["frgn_ntby_qty"] = None
            if case == "noninteger":
                document["output2"][0]["orgn_ntby_qty"] = 123
            if case == "decimal":
                document["output2"][0]["orgn_ntby_qty"] = "1.5"
            if case == "scientific":
                document["output2"][0]["orgn_ntby_qty"] = "1e3"
            if case == "bool":
                document["output2"][0]["orgn_ntby_qty"] = True
            with self.subTest(case=case):
                with self.assertRaises(InvalidKisDailyInvestorEvidence):
                    parse_kis_stock_daily(document, **args)

    def test_partial_or_bad_optional_subgroups_are_never_imputed(self):
        for field in ("ivtr_ntby_qty", "insu_ntby_qty", "fund_ntby_qty"):
            for bad in ("missing", "blank", "float", "null"):
                _, document = example()
                if bad == "missing": del document["output2"][1][field]
                if bad == "blank": document["output2"][1][field] = ""
                if bad == "float": document["output2"][1][field] = "0.5"
                if bad == "null": document["output2"][1][field] = None
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(InvalidKisDailyInvestorEvidence):
                        parse_kis_stock_daily(document)

    def test_injected_payload_or_npay_wrong_stock_is_never_approved(self):
        npay, document = example()
        npay[0]["itemCode"] = "005930"
        with self.assertRaises(ValueError):
            crosscheck_kis_stock_daily(npay, document)


if __name__ == "__main__":
    unittest.main()
