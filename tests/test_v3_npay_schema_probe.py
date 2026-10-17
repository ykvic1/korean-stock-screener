"""Offline contract tests for the single-request Npay research probe."""
import json
import unittest
from io import BytesIO
from urllib.error import HTTPError, URLError

from v3_npay_schema_probe import CODE, ENDPOINT, MAX_BYTES, probe, summarize_response


class Response:
    def __init__(self, data, url=ENDPOINT, code=200,
                 ctype="application/json"):
        self.stream=BytesIO(data)
        self.url=url
        self.code=code
        self.headers={"Content-Type":ctype}
    def geturl(self):return self.url
    def getcode(self):return self.code
    def read(self,n):return self.stream.read(n)
    def __enter__(self):return self
    def __exit__(self,*args):return False


class OneShotNpayTests(unittest.TestCase):
    def test_exactly_one_bounded_anonymous_request(self):
        observed=[]
        def opener(req,timeout):
            observed.append((req.full_url,timeout,req.get_header("Accept")))
            return Response(json.dumps({
                "content":[{"bizdate":"20261016",
                            "foreignTradeVolume":1234567,
                            "institutionTradeVolume":-34567}],
                "pageable":{"pageNumber":0},
            }).encode())
        report=probe(opener=opener)
        self.assertEqual(observed,[(ENDPOINT,12,"application/json")])
        self.assertEqual(report["stock_code"],CODE)
        self.assertEqual(report["status"],"json_schema_observed")
        self.assertEqual(report["trade_type_requested"],"KRX")
        self.assertEqual(report["nested_array_shapes"][0]["observed_items"],1)
        self.assertIn("foreignTradeVolume",report["nested_array_shapes"][0][
            "first_item_key_names"])
        self.assertNotIn("1234567",json.dumps(report))
        self.assertNotIn("-34567",json.dumps(report))
        self.assertFalse(report["flow_values_verified"])
        self.assertFalse(report["data_shape_validated"])
        self.assertFalse(report["regular_session_verified"])
        self.assertFalse(report["scoring_allowed"])
        self.assertFalse(report["v3_release_ready"])

    def test_redirect_to_other_domain_rejected(self):
        output=probe(opener=lambda req,timeout:Response(
            b'{}',url="https://untrusted.example/api"))
        self.assertEqual(output["status"],"unexpected_redirect_or_domain")
        self.assertFalse(output["scoring_allowed"])

    def test_http_error_stays_unknown_and_never_retried(self):
        calls=[]
        def failure(req,timeout):
            calls.append(1)
            raise HTTPError(ENDPOINT,403,"Forbidden",None,None)
        report=probe(opener=failure)
        self.assertEqual(len(calls),1)
        self.assertEqual(report["http_status"],403)
        self.assertFalse(report["flow_values_verified"])

    def test_non_json_html_and_oversize_never_read_as_flow(self):
        for data,ctype,status in [
            (b"<html>x</html>","text/html","response_not_json"),
            (b"x"*(MAX_BYTES+1),"application/json","response_exceeds_limit"),
            (b"{ invalid","application/json","invalid_json"),
        ]:
            with self.subTest(status=status):
                report=probe(opener=lambda req,timeout:Response(data,ctype=ctype))
                self.assertEqual(report["status"],status)
                self.assertFalse(report["scoring_allowed"])

    def test_network_failure_remains_unverified(self):
        def failure(req,timeout):raise URLError("unavailable")
        self.assertEqual(probe(opener=failure)["status"],"network_unavailable")

    def test_schema_keys_never_export_raw_values_or_claim_sessions(self):
        payload={"sections":{"items":[{"foreignNetBuy":"Sensitive Value",
                                        "private email":"user@example.com",
                                        "orgNetBuy":0}]},"token":"secret"}
        report=summarize_response(200,payload)
        text=json.dumps(report)
        self.assertNotIn("Sensitive Value",text)
        self.assertNotIn("user@example.com",text)
        self.assertNotIn("secret",text)
        self.assertNotIn("private email",text)
        self.assertFalse(report["investor_category_semantics_verified"])
        self.assertFalse(report["data_provider_licensed_for_automated_collection"])

    def test_non_success_and_non_object_fail_closed(self):
        self.assertEqual(summarize_response(503,{"x":1})["status"],
                         "http_not_success")
        self.assertEqual(summarize_response(200,"not-json-container")["status"],
                         "unexpected_json_shape")


if __name__=="__main__":
    unittest.main()
