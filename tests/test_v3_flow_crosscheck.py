"""No-network independent-flow audit and one-shot client regression tests."""
from __future__ import annotations

import json
from io import BytesIO
import unittest

import pandas as pd

from v3_flow_crosscheck import (
    CODE, audit_research_pair, from_pykrx_dataframe,
)
from v3_flow_crosscheck_once import single_trial
from v3_npay_schema_probe import ENDPOINT


def pair():
    dates=[d.date().isoformat() for d in
           pd.bdate_range(end="2026-10-16",periods=20)]
    npay=[]
    other=[]
    for i,d in enumerate(reversed(dates)):
        ins=i * 90 - 500
        foreign=800 - i * 67
        npay.append({
            "itemCode":CODE,
            "bizdate":d.replace("-",""),
            "organPureBuyQuant":f"{ins:+,d}",
            "foreignerPureBuyQuant":f"{foreign:+,d}",
        })
        other.append({
            "code":CODE, "trade_date":d, "inst_net_shares":ins,
            "foreign_net_shares":foreign,
        })
    return npay,other


class Response:
    def __init__(self, body,code=200,url=ENDPOINT,ctype="application/json"):
        self.body=BytesIO(body);self.code=code;self.url=url
        self.headers={"Content-Type":ctype}
    def geturl(self):return self.url
    def getcode(self):return self.code
    def read(self,n):return self.body.read(n)
    def __enter__(self):return self
    def __exit__(self,*a):return False


class CrossSourceSafetyTests(unittest.TestCase):
    def test_exact_match_cannot_approve_scoring_or_source_scope(self):
        npay,other=pair()
        result=audit_research_pair(npay,other)
        self.assertEqual(result["status"],
                         "twenty_days_exact_numeric_agreement_research_only")
        self.assertEqual(result["aligned_trading_days"],20)
        self.assertEqual(result["institutional_exact_match_days"],20)
        self.assertEqual(result["foreign_exact_match_days"],20)
        self.assertTrue(result["both_categories_exact_all_20"])
        for name in ("independent_licensed_official_exchange_api_verified",
                     "regular_session_and_category_definitions_verified",
                     "vendor_automated_reuse_permission_verified",
                     "confirmed_scoring_allowed","v3_release_ready"):
            self.assertIs(result[name],False)
        self.assertIsNone(result["ias_flow_component"])
        text=json.dumps(result)
        self.assertNotIn("800",text)
        self.assertNotIn("-500",text)

    def test_foreign_only_mismatch_and_missing_trading_day_are_detected(self):
        npay,other=pair()
        other[0]["foreign_net_shares"]+=1
        r=audit_research_pair(npay,other)
        self.assertEqual(r["status"],"investor_quantities_mismatch")
        self.assertEqual(r["foreign_exact_match_days"],19)
        self.assertEqual(r["institutional_exact_match_days"],20)
        self.assertFalse(r["confirmed_scoring_allowed"])
        npay,other=pair()
        other[0]["trade_date"]="2026-09-01"
        r=audit_research_pair(npay,other)
        self.assertEqual(r["status"],"trading_day_sets_differ")
        self.assertEqual(r["aligned_trading_days"],19)
        self.assertFalse(r["both_categories_exact_all_20"])

    def test_alt_wrong_dates_values_or_short_history_rejected(self):
        for case in ("duplicate","future","float","bool","null",
                     "wrongcode","short","oversized"):
            npay,other=pair()
            if case=="duplicate":other[1]["trade_date"]=other[0]["trade_date"]
            if case=="future":other[0]["trade_date"]="2026-10-17"
            if case=="float":other[0]["inst_net_shares"]=1.5
            if case=="bool":other[0]["inst_net_shares"]=False
            if case=="null":other[0]["foreign_net_shares"]=None
            if case=="wrongcode":other[0]["code"]="005930"
            if case=="short":other.pop()
            if case=="oversized":other=other*5
            with self.subTest(case=case),self.assertRaises(ValueError):
                audit_research_pair(npay,other)

    def test_pykrx_dataframe_exact_columns_and_date_index(self):
        _,other=pair()
        frame=pd.DataFrame(
            {"기관합계":[x["inst_net_shares"] for x in other],
             "외국인합계":[x["foreign_net_shares"] for x in other]},
            index=pd.to_datetime([x["trade_date"] for x in other]))
        rows=from_pykrx_dataframe(frame)
        self.assertEqual(len(rows),20)
        self.assertEqual(rows[0]["code"],CODE)
        self.assertEqual(rows[0]["trade_date"],other[0]["trade_date"])
        missing=frame.drop(columns=["기관합계"])
        with self.assertRaises(ValueError):
            from_pykrx_dataframe(missing)
        with self.assertRaises(ValueError):
            from_pykrx_dataframe(frame.iloc[:19])

    def test_live_wrapper_one_npay_and_one_alternate_query_only(self):
        npay,other=pair()
        request=[]
        query=[]
        frame=pd.DataFrame(
            {"기관합계":[x["inst_net_shares"] for x in other],
             "외국인합계":[x["foreign_net_shares"] for x in other]},
            index=pd.to_datetime([x["trade_date"] for x in other]))
        def opener(req,timeout):
            request.append((req.full_url,timeout))
            return Response(json.dumps(npay).encode())
        def investor_query(start,end,code):
            query.append((start,end,code))
            return frame
        r=single_trial(opener=opener,investor_query=investor_query)
        self.assertEqual(request,[(ENDPOINT,12)])
        self.assertEqual(query,[("20260901","20261016",CODE)])
        self.assertEqual(r["status"],
                         "twenty_days_exact_numeric_agreement_research_only")
        self.assertFalse(r["confirmed_scoring_allowed"])
        self.assertNotIn("800",json.dumps(r))

    def test_upstream_invalid_blocks_independent_request(self):
        query=[]
        def investor_query(*args):
            query.append(1)
            return None
        r=single_trial(opener=lambda req,timeout:Response(b'[]'),
                       investor_query=investor_query)
        self.assertEqual(query,[])
        self.assertEqual(r["status"],"npay_validation_or_connection_failed")
        self.assertFalse(r["confirmed_scoring_allowed"])

    def test_pykrx_provider_unavailable_is_not_a_mismatch_or_pass(self):
        npay,_=pair()
        def unavailable(*args):
            raise RuntimeError("PRIVATE_SECRET_DO_NOT_EXPOSE")
        r=single_trial(
            opener=lambda req,timeout:Response(json.dumps(npay).encode()),
            investor_query=unavailable)
        self.assertEqual(r["status"],
                         "alternate_krx_interface_unavailable_or_invalid")
        self.assertEqual(r["exception_type"],"RuntimeError")
        self.assertNotIn("PRIVATE_SECRET",json.dumps(r))
        self.assertFalse(r["independent_crosscheck_verified"])
        self.assertFalse(r["confirmed_scoring_allowed"])


if __name__ == "__main__":
    unittest.main()
