"""Contract tests for KIS investor-day response; offline only, no API key."""
from __future__ import annotations

import copy
from datetime import date
import json
import unittest

import pandas as pd

from v3_kis_investor_adapter import (
    crosscheck_kis_vs_npay, parse_kis_investor_history,
    InvalidKisInvestorEvidence, KIS_ENDPOINT, KIS_TR_ID, KIS_MARKET,
)


def pair(*, extra=0):
    dates=[d.date().strftime("%Y%m%d") for d in
           pd.bdate_range(end="2026-10-16", periods=20+extra)]
    npay=[]
    kis=[]
    for j,day in enumerate(reversed(dates)):
        i=(20+extra)-j
        institutional=i*100-900
        foreign=1800-i*50
        kis.append({
            "stck_bsop_date":day,
            "frgn_ntby_qty":f"{foreign:+,d}",
            "orgn_ntby_qty":f"{institutional:+,d}",
        })
        if j<20:
            npay.append({
                "itemCode":"252990", "bizdate":day,
                "foreignerPureBuyQuant":f"{foreign:+,d}",
                "organPureBuyQuant":f"{institutional:+,d}",
            })
    return npay,{"rt_cd":"0","output":kis}


class KisInvestorAdapterTests(unittest.TestCase):
    def test_official_example_schema_and_complete_twenty_days_parity(self):
        self.assertEqual(KIS_ENDPOINT,
                         "/uapi/domestic-stock/v1/quotations/inquire-investor")
        self.assertEqual(KIS_TR_ID,"FHKST01010900")
        self.assertEqual(KIS_MARKET,"J")
        npay,kis=pair(extra=10)
        rows=parse_kis_investor_history(kis)
        self.assertEqual(len(rows),30)
        outcome=crosscheck_kis_vs_npay(npay,kis)
        self.assertEqual(outcome["status"],
                         "twenty_days_exact_numeric_agreement_research_only")
        self.assertEqual(outcome["aligned_trading_days"],20)
        self.assertEqual(outcome["institutional_exact_match_days"],20)
        self.assertEqual(outcome["foreign_exact_match_days"],20)
        self.assertFalse(outcome["kis_live_authentication_and_service_access_verified"])
        self.assertFalse(outcome["independent_licensed_official_exchange_api_verified"])
        self.assertFalse(outcome["regular_session_and_category_definitions_verified"])
        self.assertFalse(outcome["confirmed_scoring_allowed"])
        self.assertFalse(outcome["v3_release_ready"])
        self.assertIsNone(outcome["ias_flow_component"])
        text=json.dumps(outcome)
        self.assertNotIn("frgn_ntby_qty",text)
        self.assertNotIn("orgn_ntby_qty",text)
        self.assertNotIn("stck_bsop_date",text)
        self.assertNotIn("1,800",text)

    def test_one_day_number_conflict_fails_research_agreement(self):
        npay,kis=pair()
        kis["output"][0]["frgn_ntby_qty"]="+9,999"
        result=crosscheck_kis_vs_npay(npay,kis)
        self.assertEqual(result["status"],"investor_quantities_mismatch")
        self.assertEqual(result["foreign_exact_match_days"],19)
        self.assertEqual(result["institutional_exact_match_days"],20)
        self.assertFalse(result["confirmed_scoring_allowed"])

    def test_wrong_or_missing_fields_fail_closed(self):
        for kind in ("bad_code","market","response","missing","null",
                     "noninteger","bad_date","duplicate","future",
                     "short","too_long","bad_year"):
            npay,kis=pair()
            opts={}
            if kind=="bad_code":opts["requested_code"]="005930"
            elif kind=="market":opts["market"]="NX"
            elif kind=="response":kis["rt_cd"]="1"
            elif kind=="missing":del kis["output"][0]["orgn_ntby_qty"]
            elif kind=="null":kis["output"][0]["frgn_ntby_qty"]=None
            elif kind=="noninteger":kis["output"][0]["frgn_ntby_qty"]="1.25"
            elif kind=="bad_date":kis["output"][0]["stck_bsop_date"]="20260230"
            elif kind=="duplicate":
                kis["output"][1]["stck_bsop_date"]=kis["output"][0]["stck_bsop_date"]
            elif kind=="future":kis["output"][0]["stck_bsop_date"]="20261017"
            elif kind=="short":kis["output"].pop()
            elif kind=="too_long":kis["output"]*=3
            elif kind=="bad_year":opts["asof"]="invalid"
            with self.subTest(kind=kind),self.assertRaises(InvalidKisInvestorEvidence):
                parse_kis_investor_history(kis,**opts)

    def test_window_date_set_difference_is_not_zero_count(self):
        npay,kis=pair(extra=0)
        kis["output"][19]["stck_bsop_date"]="20260901"
        result=crosscheck_kis_vs_npay(npay,kis)
        self.assertEqual(result["status"],"trading_day_sets_differ")
        self.assertEqual(result["aligned_trading_days"],19)
        self.assertFalse(result["both_categories_exact_all_20"])

    def test_npay_wrong_day_is_rejected_even_if_kis_valid(self):
        npay,kis=pair()
        npay[0]["bizdate"]="20261017"
        with self.assertRaises(ValueError):
            crosscheck_kis_vs_npay(npay,kis)


if __name__=="__main__":
    unittest.main()
