"""Offline receipt/pagination safety checks: strictly zero HTTP/network."""
from __future__ import annotations

import copy
import json
import unittest

import pandas as pd

from v3_kis_daily_pagination import (
    parse_offline_pages, compare_offline_pages_with_npay,
    InvalidKisDailyPagination, MAX_OBSERVATIONS,
)


def fixture(count=30, page_sizes=(15, 15), *, subgroups=True):
    assert sum(page_sizes) == count
    dates = [d.strftime("%Y%m%d")
             for d in pd.bdate_range(end="2026-10-16", periods=count)]
    daily, npay = [], []
    for i, trade_day in enumerate(reversed(dates)):
        org = 1200 - i*23
        foreign = i*19 - 300
        item = {
            "stck_bsop_date": trade_day,
            "orgn_ntby_qty": f"{org:+,d}",
            "frgn_ntby_qty": f"{foreign:+,d}",
        }
        if subgroups:
            item.update({
                "ivtr_ntby_qty": "+7",
                "insu_ntby_qty": "-1",
                "fund_ntby_qty": "0",
            })
        daily.append(item)
        if i < 20:
            npay.append({
                "itemCode": "252990", "bizdate": trade_day,
                "organPureBuyQuant": item["orgn_ntby_qty"],
                "foreignerPureBuyQuant": item["frgn_ntby_qty"],
            })
    receipts = []
    offset = 0
    for i, size in enumerate(page_sizes):
        receipts.append({
            "request": {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": "252990",
                "FID_INPUT_DATE_1": "20261016",
                "FID_ORG_ADJ_PRC": "",
                "FID_ETC_CLS_CODE": "",
                "request_tr_cont": "" if i == 0 else "N",
            },
            "reply": {"rt_cd": "0", "output2": daily[offset:offset+size]},
            "response_tr_cont": (
                "F" if i == 0 else "M" if i < len(page_sizes)-1 else "D"
            ),
        })
        offset += size
    return npay, receipts


class OfflineKisPaginationTests(unittest.TestCase):
    def test_bounded_two_page_parity_never_certifies_scoring(self):
        npay, pages = fixture()
        rows = parse_offline_pages(pages)
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0]["trade_date"], "2026-10-16")
        self.assertEqual(rows[0]["fund_general"], 0)
        r = compare_offline_pages_with_npay(npay, pages)
        self.assertEqual(r["status"],
                         "twenty_days_exact_numeric_agreement_research_only")
        self.assertEqual(r["aligned_trading_days"], 20)
        self.assertEqual(r["received_page_count"], 2)
        self.assertEqual(r["candidate_observed_dates"], 30)
        self.assertEqual(
            r["subgroup_coverage_rows"],
            {"investment_trust": 30, "insurance": 30, "fund_general": 30})
        for flag in ("live_response_origin_authenticated",
                     "kis_account_service_approved",
                     "genuine_exchange_trading_calendar_verified",
                     "full_history_60_120_250_certified",
                     "category_and_session_scope_verified",
                     "fund_general_equals_pension_verified",
                     "confirmed_scoring_allowed", "v3_release_ready"):
            self.assertIs(r[flag], False)
        self.assertIsNone(r["ias_flow_component"])
        rendered = json.dumps(r)
        for literal in ("orgn_ntby_qty", "frgn_ntby_qty",
                        "stck_bsop_date", "+1,200", "-300"):
            self.assertNotIn(literal, rendered)

    def test_250_synthetic_rows_not_mistaken_for_actual_full_history(self):
        npay, pages = fixture(
            250, (40, 40, 40, 40, 40, 40, 10))
        self.assertEqual(MAX_OBSERVATIONS, 250)
        r = compare_offline_pages_with_npay(npay, pages)
        self.assertEqual(r["candidate_observed_dates"], 250)
        self.assertEqual(r["received_page_count"], 7)
        self.assertTrue(r["both_categories_exact_all_20"])
        self.assertFalse(r["full_history_60_120_250_certified"])
        self.assertFalse(r["confirmed_scoring_allowed"])

    def test_wholly_absent_optional_group_remains_null(self):
        npay, pages = fixture(subgroups=False)
        data = parse_offline_pages(pages)
        self.assertIsNone(data[0]["fund_general"])
        r = compare_offline_pages_with_npay(npay, pages)
        self.assertIsNone(r["subgroup_coverage_rows"]["fund_general"])
        self.assertFalse(r["confirmed_scoring_allowed"])

    def test_mismatch_in_one_day_never_promotes_score(self):
        npay, pages = fixture()
        pages[1]["reply"]["output2"][1]["frgn_ntby_qty"] = "9999"
        result = compare_offline_pages_with_npay(npay, pages)
        self.assertEqual(result["status"], "investor_quantities_mismatch")
        self.assertEqual(result["foreign_exact_match_days"], 19)
        self.assertFalse(result["confirmed_scoring_allowed"])

    def test_truncated_or_extra_continuation_rejected(self):
        _, pages = fixture()
        cases = []
        x = copy.deepcopy(pages)
        x.pop()
        cases.append(x)
        x = copy.deepcopy(pages)
        x[0]["response_tr_cont"] = ""
        cases.append(x)
        x = copy.deepcopy(pages)
        x[1]["response_tr_cont"] = "M"
        cases.append(x)
        x = copy.deepcopy(pages)
        x[1]["request"]["request_tr_cont"] = ""
        cases.append(x)
        x = copy.deepcopy(pages)
        x[0]["response_tr_cont"] = "N"
        cases.append(x)
        x = copy.deepcopy(pages)
        x[0]["response_tr_cont"] = None
        cases.append(x)
        for i, received in enumerate(cases):
            with self.subTest(i=i), self.assertRaises(InvalidKisDailyPagination):
                parse_offline_pages(received)

    def test_page_overlap_non_descending_order_and_future_day_rejected(self):
        _, pages = fixture()
        cases = []
        x = copy.deepcopy(pages)
        x[1]["reply"]["output2"][0]["stck_bsop_date"] = (
            x[0]["reply"]["output2"][-1]["stck_bsop_date"])
        cases.append(x)
        x = copy.deepcopy(pages)
        x[0]["reply"]["output2"][1],x[0]["reply"]["output2"][2] = (
            x[0]["reply"]["output2"][2],x[0]["reply"]["output2"][1])
        cases.append(x)
        x = copy.deepcopy(pages)
        x[0]["reply"]["output2"][0]["stck_bsop_date"] = "20261017"
        cases.append(x)
        for idx, received in enumerate(cases):
            with self.subTest(idx=idx), self.assertRaises(InvalidKisDailyPagination):
                parse_offline_pages(received)

    def test_wrong_response_schema_codes_and_request_identity_rejected(self):
        _, pages = fixture()
        variants = []
        x = copy.deepcopy(pages)
        x[0]["reply"]["rt_cd"] = "1"
        variants.append(x)
        x = copy.deepcopy(pages)
        x[0]["reply"]["output"] = x[0]["reply"].pop("output2")
        variants.append(x)
        x = copy.deepcopy(pages)
        x[1]["request"]["FID_COND_MRKT_DIV_CODE"] = "UN"
        variants.append(x)
        x = copy.deepcopy(pages)
        x[1]["request"]["FID_INPUT_ISCD"] = "005930"
        variants.append(x)
        x = copy.deepcopy(pages)
        x[1]["request"]["FID_INPUT_DATE_1"] = "20261015"
        variants.append(x)
        x = copy.deepcopy(pages)
        x[1]["request"]["FID_ORG_ADJ_PRC"] = "1"
        variants.append(x)
        x = copy.deepcopy(pages)
        x[0]["request"]["arbitrary_extra_flag"] = True
        variants.append(x)
        for i, received in enumerate(variants):
            with self.subTest(i=i), self.assertRaises(InvalidKisDailyPagination):
                parse_offline_pages(received)

    def test_null_partial_missing_and_non_integer_never_imputed(self):
        _, pages = fixture()
        variants = []
        for key, value in (("orgn_ntby_qty", None),
                           ("frgn_ntby_qty", "1.2"),
                           ("ivtr_ntby_qty", None),
                           ("insu_ntby_qty", "1e5"),
                           ("fund_ntby_qty", 0)):
            x = copy.deepcopy(pages)
            x[1]["reply"]["output2"][0][key] = value
            variants.append(x)
        x = copy.deepcopy(pages)
        del x[1]["reply"]["output2"][0]["fund_ntby_qty"]
        variants.append(x)
        for i, received in enumerate(variants):
            with self.subTest(i=i), self.assertRaises(InvalidKisDailyPagination):
                parse_offline_pages(received)

    def test_pages_and_global_limits_are_enforced(self):
        _, pages = fixture(20, (20,))
        self.assertEqual(len(parse_offline_pages(pages)), 20)
        for received in ([], pages*17, [dict(pages[0], reply={
                "rt_cd": "0", "output2": pages[0]["reply"]["output2"]*3})]):
            with self.assertRaises(InvalidKisDailyPagination):
                parse_offline_pages(received)
        _, long_pages = fixture(251, (40,40,40,40,40,40,11))
        with self.assertRaises(InvalidKisDailyPagination):
            parse_offline_pages(long_pages)

    def test_invalid_asof_or_npay_identity_never_passes(self):
        npay, pages = fixture()
        for kwargs in ({"asof":"2026-02-30"},
                       {"request_date":"20261015"},
                       {"market":"NX"},
                       {"code":"005930"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(
                    InvalidKisDailyPagination):
                parse_offline_pages(pages, **kwargs)
        npay[0]["itemCode"]="005930"
        with self.assertRaises(ValueError):
            compare_offline_pages_with_npay(npay, pages)


if __name__ == "__main__":
    unittest.main()
