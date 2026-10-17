"""Completely offline tests for the manual Kiwoom live pilot; no keys or network."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from v3_kiwoom_readonly_pilot import KiwoomPilotError, main, probe


def rows(count=5, *, include_pension=True):
    from datetime import date, timedelta
    current = date(2026, 10, 16)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            r = {"dt": current.strftime("%Y%m%d"), "orgn": "-1,250",
                 "frgnr_invsr": "+900"}
            if include_pension:
                r["penfnd_etc"] = "0"
            result.append(r)
        current -= timedelta(days=1)
    return result


def fake_post(*, response_rows=None, token_code=0, flow_code=0,
              raise_after_token=False):
    calls = []
    def inner(url, payload, headers):
        calls.append((url, payload, headers))
        if len(calls) == 1:
            return {"return_code": token_code, "token": "DO_NOT_PRINT_FAKE_TOKEN"}
        if raise_after_token:
            raise KiwoomPilotError("api_network_or_ip_failure")
        return {"return_code": flow_code,
                "stk_invsr_orgn": response_rows if response_rows is not None else rows()}
    return inner, calls


class ReadOnlyKiwoomPilotTests(unittest.TestCase):
    def run_candidate(self, post, **overrides):
        args = dict(code="252990", asof="20261016", mode="real",
                    app_key="FAKE_KEY", app_secret="FAKE_SECRET", post=post)
        args.update(overrides)
        return probe(**args)

    def test_two_calls_only_correct_readonly_endpoints_and_units(self):
        post, calls = fake_post()
        out = self.run_candidate(post)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "https://api.kiwoom.com/oauth2/token")
        self.assertEqual(calls[1][0], "https://api.kiwoom.com/api/dostk/stkinfo")
        self.assertEqual(calls[1][1],
                         {"dt": "20261016", "stk_cd": "252990",
                          "amt_qty_tp": "2", "trde_tp": "0", "unit_tp": "1"})
        self.assertEqual(calls[1][2]["api-id"], "ka10059")
        self.assertEqual(calls[1][2]["authorization"], "Bearer DO_NOT_PRINT_FAKE_TOKEN")
        self.assertTrue(out["institution_foreign_signed_quantity_schema_valid"])
        self.assertTrue(out["pension_etc_field_complete"])
        self.assertEqual(out["received_rows_one_page"], 5)
        self.assertFalse(out["confirmed_scoring_allowed"])
        self.assertFalse(out["v3_release_ready"])
        self.assertIsNone(out["ias_flow_component"])
        s = json.dumps(out)
        for forbidden in ("FAKE_KEY", "FAKE_SECRET", "DO_NOT_PRINT_FAKE_TOKEN",
                          "1,250", "900", "account", "order", "ip_address"):
            self.assertNotIn(forbidden, s)

    def test_demo_selects_only_mock_host(self):
        post, calls = fake_post()
        out = self.run_candidate(post, mode="demo")
        self.assertEqual(out["mode"], "demo")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(url.startswith("https://mockapi.kiwoom.com/") for url, _, _ in calls))

    def test_missing_secrets_invalid_codes_mode_and_future_fail_before_any_call(self):
        for changed in (
            {"app_key": ""}, {"app_secret": ""}, {"mode": "unknown"},
            {"code": "bad"}, {"asof": "20260230"}, {"asof": "20990101"},
        ):
            post, calls = fake_post()
            with self.subTest(changed=changed), self.assertRaises(KiwoomPilotError):
                self.run_candidate(post, **changed)
            self.assertEqual(calls, [])

    def test_invalid_token_stops_before_flow_query(self):
        for token_code in (-1, "100", None):
            post, calls = fake_post(token_code=token_code)
            with self.assertRaises(KiwoomPilotError):
                self.run_candidate(post)
            self.assertEqual(len(calls), 1)

    def test_api_flow_error_or_ip_rejection_fails_without_retry(self):
        post, calls = fake_post(flow_code=-2)
        with self.assertRaisesRegex(KiwoomPilotError, "flow_query_not_accepted"):
            self.run_candidate(post)
        self.assertEqual(len(calls), 2)
        post, calls = fake_post(raise_after_token=True)
        with self.assertRaises(KiwoomPilotError):
            self.run_candidate(post)
        self.assertEqual(len(calls), 2)

    def test_malformed_rows_do_not_convert_missing_or_noninteger_to_zero(self):
        variants = []
        for field, value in (("orgn", None), ("frgnr_invsr", "1.2"),
                             ("frgnr_invsr", 40), ("penfnd_etc", "-1e4")):
            data = rows()
            data[0][field] = value
            variants.append(data)
        data = rows()
        del data[0]["penfnd_etc"]
        variants.append(data)
        data = rows()
        data[1]["dt"] = data[0]["dt"]
        variants.append(data)
        data = rows()
        data[0]["dt"] = "20261019"
        variants.append(data)
        data = rows()
        data[0], data[1] = data[1], data[0]
        variants.append(data)
        for i, candidate in enumerate(variants):
            post, calls = fake_post(response_rows=candidate)
            with self.subTest(i=i), self.assertRaises(KiwoomPilotError):
                self.run_candidate(post)
            self.assertEqual(len(calls), 2)

    def test_absent_all_pension_is_null_coverage_not_zero(self):
        post, _ = fake_post(response_rows=rows(include_pension=False))
        out = self.run_candidate(post)
        self.assertFalse(out["pension_etc_field_complete"])

    def test_empty_or_oversized_page_is_not_proof_of_20_day_history(self):
        for candidate in ([], rows(251)):
            post, _ = fake_post(response_rows=candidate)
            with self.assertRaises(KiwoomPilotError):
                self.run_candidate(post)

    def test_cli_no_live_flag_no_network_even_with_keys(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            status = main(["--mode", "real", "--asof", "20261016"])
        self.assertEqual(status, 2)
        self.assertIn("pilot_not_run", buf.getvalue())

    def test_cli_on_unknown_error_does_not_echo_secret(self):
        with patch("v3_kiwoom_readonly_pilot.probe",
                   side_effect=ValueError("FAKE_SECRET")):
            with patch.dict(os.environ, {"KIWOOM_APP_KEY": "FAKE_KEY",
                                         "KIWOOM_APP_SECRET": "FAKE_SECRET"}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    status = main(["--live", "--mode", "real", "--asof", "20261016"])
        self.assertEqual(status, 1)
        self.assertEqual(buf.getvalue().strip(), "pilot_failed: unexpected_local_error")


if __name__ == "__main__":
    unittest.main()
