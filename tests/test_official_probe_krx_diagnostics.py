"""Adversarial KRX live diagnostics: never echo secret, response, URL or payload.

All simulated status codes and response bodies are synthetic. No network.
"""
from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from official_probe.clients import KRXClient, KRXProbeError
from official_probe.__main__ import run
from official_probe.ci_gate import (
    enforce_live_krx_result, LiveProbeGateError, cli,
)
from test_official_probe import Response, CaptureSession, item

ASOF = "2026-10-16"
SECRET = "KRX_PRIVATE_HEADER_TOKEN_DO_NOT_PRINT"
BODY = "secret=KRX_PRIVATE_HEADER_TOKEN_DO_NOT_PRINT&personal=PRIVATE"


class KRXDiagnosticTests(unittest.TestCase):
    def test_http_statuses_are_safe_and_exact_no_vendor_message(self):
        expected = {
            401:"http_unauthorized", 403:"http_forbidden",
            404:"http_not_found", 429:"http_rate_limited",
            500:"http_server_error", 503:"http_server_error",
            302:"http_other", 400:"http_other",
        }
        for status, reason in expected.items():
            with self.subTest(status=status):
                session = CaptureSession([Response(BODY, status)])
                with self.assertRaises(KRXProbeError) as error:
                    KRXClient(SECRET, session=session).daily_market("KOSDAQ", ASOF)
                exc = error.exception
                self.assertEqual(exc.reason, reason)
                self.assertEqual(exc.http_status, status)
                self.assertEqual(exc.market, "KOSDAQ")
                self.assertNotIn(SECRET, str(exc))
                self.assertNotIn(BODY, str(exc))
                self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_invalid_json_schema_missing_rows_and_bad_transport(self):
        failures=[
            (Response(b"<html>blocked " + BODY.encode() + b"</html>"),"invalid_json",200),
            (Response({"status":"E09","msg":BODY}),"missing_outblock_1",200),
            (Response({"OutBlock_1":[]}),"empty_market_rows",200),
            (Response({"OutBlock_1":[12]}),"invalid_market_rows",200),
            (Response(b""),"empty_body",200),
            (RuntimeError(BODY),"transport_failure",None),
        ]
        for response,category,status in failures:
            with self.subTest(reason=category), self.assertRaises(KRXProbeError) as error:
                KRXClient(SECRET,session=CaptureSession([response])).daily_market("KOSPI",ASOF)
            exc=error.exception
            self.assertEqual((exc.reason,exc.http_status),(category,status))
            self.assertNotIn(SECRET,str(exc))
            self.assertNotIn(BODY,str(exc))

    def test_api_failure_report_is_specific_safe_and_not_scoring_ready(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/"data.json"
            out=Path(t)/"private.json"
            source.write_text(json.dumps({"date":ASOF,"stocks":[
                {"code":"005930","market":"KOSPI"},
                {"code":"247540","market":"KOSDAQ"}
            ]}))
            session=CaptureSession([Response(BODY,403)])
            with patch.dict(os.environ,{"KRX_API_KEY":SECRET}):
                result=run([
                    "--asof",ASOF,"--source",str(source),
                    "--provider","krx","--live","--output",str(out)
                ],krx_session=session)
            self.assertEqual(result["krx"]["status"],"failed")
            self.assertEqual(result["krx"]["diagnostic"],{
                "market":"KOSPI","reason":"http_forbidden","http_status":403})
            self.assertFalse(result["investment_data_ready"])
            self.assertFalse(result["confirmed_scoring_allowed"])
            saved=out.read_text()
            self.assertNotIn(BODY,saved)
            self.assertNotIn(SECRET,saved)
            self.assertNotIn("AUTH_KEY",saved)
            self.assertEqual(len(session.calls),1)

    def test_invalid_observed_ohlc_reports_validation_not_raw_value(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/"data.json"
            out=Path(t)/"report.json"
            source.write_text(json.dumps({"date":ASOF,"stocks":[
                {"code":"005930","market":"KOSPI"}]}))
            session=CaptureSession([Response({"OutBlock_1":[
                item(TDD_CLSPRC="-") ]})])
            with patch.dict(os.environ,{"KRX_API_KEY":SECRET}):
                r=run(["--asof",ASOF,"--source",str(source),"--provider","krx",
                       "--live","--output",str(out)],krx_session=session)
            self.assertEqual(r["krx"]["status"],"failed")
            self.assertEqual(r["krx"]["diagnostic"]["reason"],
                             "response_validation_failed")
            self.assertIsNone(r["krx"]["diagnostic"]["http_status"])
            self.assertNotIn(SECRET,out.read_text())

    def test_gate_does_not_turn_successful_python_run_into_false_green(self):
        failed={"asof":ASOF,"mode":"live","krx":{
            "status":"failed","diagnostic":{"market":"KOSPI",
                                            "reason":"http_forbidden",
                                            "http_status":403}}}
        with self.assertRaisesRegex(LiveProbeGateError,"live_krx_not_observed"):
            enforce_live_krx_result(failed,mode="live",provider="krx")
        with self.assertRaises(LiveProbeGateError):
            enforce_live_krx_result({"krx":{"status":"key_required"}},
                                    mode="live",provider="krx")
        self.assertEqual(enforce_live_krx_result(failed,mode="dry_run",provider="krx"),
                         "not_a_live_krx_job")
        self.assertEqual(enforce_live_krx_result(failed,mode="live",provider="dart"),
                         "not_a_live_krx_job")

    def test_gate_allows_only_sample_observed_but_never_approved_scoring(self):
        sample={
            "asof":ASOF,"mode":"live","investment_data_ready":False,
            "confirmed_scoring_allowed":False,
            "krx":{"status":"observed_unverified",
                   "markets":[{"market":"KOSPI","found_symbols":["005930"]}]}}
        self.assertEqual(
            enforce_live_krx_result(sample,mode="live",provider="krx"),
            "krx_http_sample_observed_not_scoring_verified")
        bad=dict(sample)
        bad["investment_data_ready"]=True
        with self.assertRaisesRegex(LiveProbeGateError,"invalid_unsafe"):
            enforce_live_krx_result(bad,mode="live",provider="krx")
        bad={**sample,"krx":{"status":"observed_unverified","markets":[]}}
        with self.assertRaises(LiveProbeGateError):
            enforce_live_krx_result(bad,mode="live",provider="krx")

    def test_ci_gate_cli_reads_only_exact_asof_safe_output_and_fails(self):
        with tempfile.TemporaryDirectory() as t:
            old=os.getcwd()
            os.chdir(t)
            try:
                folder=Path("evidence/official_probe")
                folder.mkdir(parents=True)
                folder.joinpath("probe_20261016.json").write_text(
                    json.dumps({"asof":ASOF,"mode":"live","krx":{"status":"failed"}}))
                with patch("sys.stdout",new_callable=StringIO) as output:
                    rc=cli(["--asof",ASOF,"--mode","live","--provider","krx"])
                self.assertEqual(rc,1)
                self.assertIn("missing_invalid_or_failed_probe",output.getvalue())
                self.assertNotIn(SECRET,output.getvalue())
                with patch("sys.stdout",new_callable=StringIO):
                    rc=cli(["--asof","2026-10-15","--mode","live","--provider","krx"])
                self.assertEqual(rc,1)
            finally:
                os.chdir(old)


if __name__=="__main__":
    unittest.main()
