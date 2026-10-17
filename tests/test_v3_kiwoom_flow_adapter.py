"""Offline Kiwoom ka10059 regression tests; no API access or credentials."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import json
import unittest

from v3_kiwoom_flow_adapter import (
    API_ID, PATH, MAX_ROWS, InvalidKiwoomFlowEvidence,
    normalize_offline_receipts, audit_kiwoom_against_npay,
)


def fixture(size=30, per_page=(15, 15), *, subjects=True):
    assert sum(per_page) == size
    dates = []
    day = date(2026, 10, 16)
    while len(dates) < size:
        if day.weekday() < 5:
            dates.append(day.strftime("%Y%m%d"))
        day -= timedelta(days=1)
    obs, npay = [], []
    for i, current in enumerate(dates):
        inst = i * 13 - 200
        foreign = 800 - i * 21
        record = {"dt": current, "orgn": f"{inst:+,d}",
                  "frgnr_invsr": f"{foreign:+,d}"}
        if subjects:
            record.update({"penfnd_etc": "-3", "invtrt": "+9",
                           "insrnc": "0", "fnnc_invt": "+1",
                           "samo_fund": "0"})
        obs.append(record)
        if i < 20:
            npay.append({"itemCode": "252990", "bizdate": current,
                         "organPureBuyQuant": record["orgn"],
                         "foreignerPureBuyQuant": record["frgnr_invsr"]})
    receipts, offset = [], 0
    for i, count in enumerate(per_page):
        more = i < len(per_page) - 1
        receipts.append({
            "request": {"api_id": API_ID, "path": PATH,
                        "body": {"dt": "20261016", "stk_cd": "252990",
                                 "amt_qty_tp": "2", "trde_tp": "0",
                                 "unit_tp": "1"},
                        "cont_yn": "" if i == 0 else "Y",
                        "next_key": "" if i == 0 else f"page-{i}"},
            "response": {"return_code": 0,
                         "stk_invsr_orgn": obs[offset:offset+count]},
            "response_cont_yn": "Y" if more else "N",
            "response_next_key": f"page-{i+1}" if more else "",
        })
        offset += count
    return npay, receipts


class KiwoomOfflineFlowTests(unittest.TestCase):
    def test_correct_net_share_unit_and_optional_categories(self):
        _, pages = fixture()
        out = normalize_offline_receipts(pages)
        self.assertEqual(len(out), 30)
        self.assertEqual(out[0]["trade_date"], "2026-10-16")
        self.assertEqual(out[0]["inst_net_shares"], -200)
        self.assertEqual(out[0]["pension_etc_net_shares"], -3)

    def test_missing_subgroup_is_null_not_zero(self):
        _, pages = fixture(subjects=False)
        out = normalize_offline_receipts(pages)
        self.assertIsNone(out[0]["pension_etc_net_shares"])
        self.assertEqual(out[0]["foreign_net_shares"], 800)

    def test_wrong_request_tr_code_market_unit_or_action_fails(self):
        for field, wrong in (("amt_qty_tp", "1"), ("unit_tp", "1000"),
                             ("trde_tp", "1"), ("stk_cd", "005930"),
                             ("dt", "20261015")):
            _, pages = fixture()
            pages[0]["request"]["body"][field] = wrong
            with self.subTest(field=field), self.assertRaises(InvalidKiwoomFlowEvidence):
                normalize_offline_receipts(pages)
        _, pages = fixture()
        pages[0]["request"]["api_id"] = "ka10061"
        with self.assertRaises(InvalidKiwoomFlowEvidence):
            normalize_offline_receipts(pages)

    def test_truncated_wrong_header_next_key_and_unrequested_extra_fail(self):
        variants = []
        _, pages = fixture()
        variants.append(pages[:1])
        _, pages = fixture()
        pages[1]["request"]["next_key"] = "different"
        variants.append(pages)
        _, pages = fixture()
        pages[0]["response_cont_yn"] = "N"
        pages[0]["response_next_key"] = ""
        variants.append(pages)
        _, pages = fixture()
        pages[0]["response_next_key"] = ""
        variants.append(pages)
        _, pages = fixture()
        pages[1]["request"]["cont_yn"] = "N"
        variants.append(pages)
        for i, candidate in enumerate(variants):
            with self.subTest(case=i), self.assertRaises(InvalidKiwoomFlowEvidence):
                normalize_offline_receipts(candidate)

    def test_duplicate_future_out_of_order_and_invalid_date_fail(self):
        variants = []
        _, pages = fixture()
        pages[1]["response"]["stk_invsr_orgn"][0]["dt"] = (
            pages[0]["response"]["stk_invsr_orgn"][-1]["dt"])
        variants.append(pages)
        _, pages = fixture()
        x = pages[1]["response"]["stk_invsr_orgn"]
        x[1], x[2] = x[2], x[1]
        variants.append(pages)
        _, pages = fixture()
        pages[0]["response"]["stk_invsr_orgn"][0]["dt"] = "20261020"
        variants.append(pages)
        _, pages = fixture()
        pages[0]["response"]["stk_invsr_orgn"][0]["dt"] = "20260230"
        variants.append(pages)
        for i, candidate in enumerate(variants):
            with self.subTest(case=i), self.assertRaises(InvalidKiwoomFlowEvidence):
                normalize_offline_receipts(candidate)

    def test_malformed_share_optional_partial_and_bad_response_fail(self):
        for field, value in (("orgn", None), ("frgnr_invsr", "1.5"),
                             ("frgnr_invsr", 0), ("orgn", "1e6"),
                             ("penfnd_etc", None)):
            _, pages = fixture()
            pages[0]["response"]["stk_invsr_orgn"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(InvalidKiwoomFlowEvidence):
                normalize_offline_receipts(pages)
        _, pages = fixture()
        pages[1]["response"]["stk_invsr_orgn"][0].pop("penfnd_etc")
        with self.assertRaises(InvalidKiwoomFlowEvidence):
            normalize_offline_receipts(pages)
        _, pages = fixture()
        pages[0]["response"]["return_code"] = -100
        with self.assertRaises(InvalidKiwoomFlowEvidence):
            normalize_offline_receipts(pages)

    def test_one_to_250_offline_window_bounds_only_no_real_history_claim(self):
        _, pages = fixture(size=250, per_page=(25,) * 10)
        self.assertEqual(MAX_ROWS, 250)
        self.assertEqual(len(normalize_offline_receipts(pages)), 250)
        _, pages = fixture(size=251, per_page=(25,) * 9 + (26,))
        with self.assertRaises(InvalidKiwoomFlowEvidence):
            normalize_offline_receipts(pages)
        _, pages = fixture(size=19, per_page=(19,))
        with self.assertRaises(InvalidKiwoomFlowEvidence):
            normalize_offline_receipts(pages)

    def test_twenty_day_exact_research_match_still_no_ias_score(self):
        npay, pages = fixture()
        out = audit_kiwoom_against_npay(npay, pages)
        self.assertTrue(out["both_categories_exact_all_20"])
        self.assertEqual(out["institutional_exact_match_days"], 20)
        self.assertEqual(out["foreign_exact_match_days"], 20)
        self.assertEqual(out["subgroup_coverage_rows"]["pension_etc_net_shares"], 30)
        for field in ("kiwoom_api_live_access_verified", "live_origin_authenticated",
                      "krx_regular_session_verified", "pension_only_identity_verified",
                      "confirmed_scoring_allowed", "v3_release_ready"):
            self.assertIs(out[field], False)
        self.assertIsNone(out["ias_flow_component"])
        self.assertNotIn("penfnd_etc", json.dumps(out))

    def test_one_day_numeric_mismatch_fails_agreement(self):
        npay, pages = fixture()
        pages[0]["response"]["stk_invsr_orgn"][0]["orgn"] = "+99999"
        out = audit_kiwoom_against_npay(npay, pages)
        self.assertEqual(out["status"], "investor_quantities_mismatch")
        self.assertEqual(out["institutional_exact_match_days"], 19)
        self.assertFalse(out["confirmed_scoring_allowed"])


if __name__ == "__main__":
    unittest.main()
