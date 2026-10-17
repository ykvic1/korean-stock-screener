"""Offline, fail-closed numeric Npay research parser tests."""
import json
import unittest
from datetime import date,timedelta
from io import BytesIO
from urllib.error import HTTPError

from v3_npay_schema_probe import ENDPOINT
from v3_npay_numeric_probe import (
    normalize_anonymous_npay_rows, probe_numeric_rows, UnverifiedFlowShape)


def fixture():
    base=date(2026,10,16)
    return [
        {"itemCode":"252990","bizdate":(base-timedelta(days=i)).strftime("%Y%m%d"),
         "organPureBuyQuant":("-1,204" if i%2 else "0"),
         "foreignerPureBuyQuant":("+21,603" if i%2 else "-2,008")}
        for i in range(20)]


class Response:
    def __init__(self,data,url=ENDPOINT,status=200,ctype="application/json"):
        self.data=BytesIO(data)
        self.url=url;self.status=status;self.headers={"Content-Type":ctype}
    def geturl(self):return self.url
    def getcode(self):return self.status
    def read(self,n):return self.data.read(n)
    def __enter__(self):return self
    def __exit__(self,*a):return False


class NumericNpayProbeTests(unittest.TestCase):
    def test_exact_20_rows_numeric_strings_are_uncertified(self):
        result=normalize_anonymous_npay_rows(fixture())
        self.assertEqual(result["observed_numeric_rows"],20)
        self.assertTrue(result["windows"]["20"]["numerical_window_complete"])
        self.assertEqual(result["windows"]["20"]["inst_positive_days"],0)
        self.assertEqual(result["windows"]["20"]["foreign_positive_days"],10)
        self.assertFalse(result["confirmed_scoring_allowed"])
        self.assertFalse(result["investor_flow_scoring_allowed"])
        self.assertFalse(result["trading_calendar_and_session_independently_verified"])
        for period in ("60","120","250"):
            self.assertIsNone(result["windows"][period]["foreign_net_shares"])

    def test_numeric_probe_one_anonymous_get_and_redacted_result(self):
        observed=[]
        def opener(req,timeout):
            observed.append((req.full_url,timeout))
            return Response(json.dumps(fixture()).encode())
        report=probe_numeric_rows(opener=opener)
        self.assertEqual(observed,[(ENDPOINT,12)])
        self.assertEqual(report["status"],
                         "twenty_numeric_rows_parsed_as_unverified_candidate")
        self.assertTrue(report["window_20_numerically_complete"])
        self.assertFalse(report["independent_source_crosscheck_passed"])
        self.assertFalse(report["confirmed_scoring_allowed"])
        self.assertFalse(report["regular_session_and_market_verified"])
        self.assertNotIn("21603",json.dumps(report))
        self.assertNotIn("1204",json.dumps(report))

    def test_malformed_rows_rejected_without_imputation(self):
        for kind in ("missing","null","float","bool","not_number","decimal",
                     "wrong_code","future","duplicate","insufficient",
                     "too_many","bad_date","negative_paren","bad_commas"):
            with self.subTest(kind=kind),self.assertRaises(UnverifiedFlowShape):
                rows=fixture()
                if kind=="missing":del rows[0]["organPureBuyQuant"]
                elif kind=="null":rows[0]["organPureBuyQuant"]=None
                elif kind=="float":rows[0]["organPureBuyQuant"]=0.0
                elif kind=="bool":rows[0]["organPureBuyQuant"]=True
                elif kind=="not_number":rows[0]["organPureBuyQuant"]="-"
                elif kind=="decimal":rows[0]["organPureBuyQuant"]="2.5"
                elif kind=="wrong_code":rows[0]["itemCode"]="005930"
                elif kind=="future":rows[0]["bizdate"]="20261017"
                elif kind=="duplicate":rows[1]["bizdate"]=rows[0]["bizdate"]
                elif kind=="insufficient":rows.pop()
                elif kind=="too_many":rows.append(dict(rows[0]))
                elif kind=="bad_date":rows[0]["bizdate"]="20260230"
                elif kind=="negative_paren":rows[0]["organPureBuyQuant"]="(200)"
                elif kind=="bad_commas":rows[0]["organPureBuyQuant"]="2,01"
                normalize_anonymous_npay_rows(rows)

    def test_dot_date_formats_and_real_zero_are_valid_observations_only(self):
        rows=fixture()
        rows[0]["bizdate"]="26. 10. 16."
        rows[1]["bizdate"]="2026-10-15"
        summary=normalize_anonymous_npay_rows(rows)
        self.assertEqual(summary["windows"]["20"]["inst_positive_days"],0)
        self.assertFalse(summary["market_participant_identity_verified"])

    def test_unsafe_redirect_and_html_never_become_data(self):
        for url,ctype,state in [
            ("https://other-site.example/api","application/json","unexpected_redirect"),
            (ENDPOINT,"text/html","non_json")
        ]:
            with self.subTest(state=state):
                report=probe_numeric_rows(opener=lambda req,timeout:Response(
                    json.dumps(fixture()).encode(),url=url,ctype=ctype))
                self.assertEqual(report["status"],state)
                self.assertFalse(report["scoring_allowed"])

    def test_http_error_does_not_fallback_to_unknown_supplier(self):
        count=[]
        def bad(req,timeout):
            count.append(1)
            raise HTTPError(ENDPOINT,429,"Rate limited",None,None)
        result=probe_numeric_rows(opener=bad)
        self.assertEqual(len(count),1)
        self.assertEqual(result["status"],"http_error")
        self.assertEqual(result["code"],429)
        self.assertFalse(result["scoring_allowed"])


if __name__=="__main__":
    unittest.main()
