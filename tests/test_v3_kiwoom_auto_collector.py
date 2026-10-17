"""Offline/credential-free tests for future registered-IP automatic collection."""
from __future__ import annotations

from datetime import date, timedelta
import io
import os
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

import v3_kiwoom_auto_collector as c


def rows(count=260, with_subgroups=True):
    d = date(2026, 10, 16)
    out = []
    while len(out) < count:
        if d.weekday() < 5:
            row = {"dt": d.strftime("%Y%m%d"), "orgn": "-1,250",
                   "frgnr_invsr": "+600"}
            if with_subgroups:
                row.update({"penfnd_etc": "+5", "invtrt": "-10",
                            "insrnc": "0", "fnnc_invt": "+3", "samo_fund": "-1"})
            out.append(row)
        d -= timedelta(days=1)
    return out


def transport(*, observations=None, broken_at=None, fail_code=None):
    raw = rows() if observations is None else observations
    calls = []
    def post(url, body, headers):
        calls.append((url, body, headers))
        if url.endswith("/oauth2/token"):
            return {"body": {"token": "DO_NOT_LOG_SYNTHETIC_TOKEN",
                              "token_type": "bearer"}}
        if fail_code is not None and body["stk_cd"] == fail_code:
            return {"body": {"return_code": 999, "return_msg": "SENSITIVE_REMOTE_VALUE"}}
        page = sum(1 for u, _, _ in calls if u.endswith("/api/dostk/stkinfo")) - 1
        if broken_at is not None and page == broken_at:
            return {"body": {"return_code": 0, "stk_invsr_orgn": raw[99:199]},
                    "cont_yn": "Y", "next_key": "cursor_2"}
        section = raw[page * 100:(page + 1) * 100]
        more = (page + 1) * 100 < len(raw)
        return {"body": {"return_code": 0, "stk_invsr_orgn": section},
                "cont_yn": "Y" if more else "N",
                "next_key": "cursor_" + str(page + 1) if more else ""}
    return post, calls


class KiwoomAutoCollectorTests(unittest.TestCase):
    def test_no_live_flag_never_collects(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = c.main(["--codes", "252990"])
        self.assertEqual(result, 2)
        self.assertIn("not_run", buffer.getvalue())

    def test_code_validation_and_35_limit(self):
        for invalid in ([], ["999999"] * 2, ["252990", "abc"],
                        [str(i).zfill(6) for i in range(36)]):
            with self.assertRaises(c.KiwoomCollectionError):
                c._codes(invalid)
        self.assertEqual(c._codes(["252990", "005930"]), ["252990", "005930"])

    def test_signed_shares_not_amounts_or_blank(self):
        for malformed in (None, 10, "5.5", "1e5", "", "a", "NaN"):
            with self.assertRaises(c.KiwoomCollectionError):
                c._signed(malformed)
        self.assertEqual(c._signed("-1,250"), -1250)
        self.assertEqual(c._signed("0"), 0)

    def test_two_or_three_page_pagination_and_signed_history_features(self):
        post, calls = transport()
        token = c._token("FAKE_KEY", "FAKE_SECRET", post)
        out = c.fetch_one("252990", "20261016", token, post=post,
                          pause=lambda _: None)
        self.assertEqual(len(calls), 4)  # 1 OAuth, 3 data pages
        self.assertEqual(calls[0][0], c.BASE + "/oauth2/token")
        self.assertTrue(all(x[0].endswith("/api/dostk/stkinfo") for x in calls[1:]))
        self.assertEqual(calls[1][1],
                         {"dt": "20261016", "stk_cd": "252990",
                          "amt_qty_tp": "2", "trde_tp": "0", "unit_tp": "1"})
        self.assertNotIn("cont-yn", calls[1][2])
        self.assertEqual(calls[2][2]["cont-yn"], "Y")
        self.assertEqual(calls[2][2]["next-key"], "cursor_1")
        self.assertEqual(calls[3][2]["next-key"], "cursor_2")
        self.assertEqual(out["row_count"], 260)
        self.assertFalse(out["older_pages_available"])
        self.assertEqual(out["windows"]["250"]["inst_sum_shares"], -312500)
        self.assertEqual(out["windows"]["60"]["pension_etc_sum_shares"], 300)
        self.assertEqual(out["windows"]["20"]["foreign_positive_days"], 20)
        self.assertFalse(out["confirmed_scoring_allowed"])
        self.assertFalse(out["source_agreement_check_required"])
        self.assertIsNone(out["ias_flow_component"])

    def test_one_page_does_not_pretend_to_have_120_or_250_days(self):
        post, calls = transport()
        out = c.fetch_one("252990", "20261016", "FAKE_TOKEN", post=post,
                          pause=lambda _: None, max_pages=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["row_count"], 100)
        self.assertTrue(out["older_pages_available"])
        self.assertFalse(out["provider_reports_no_more_pages"])
        self.assertFalse(out["official_history_completeness_verified"])
        self.assertIsNone(out["windows"]["120"])
        self.assertIsNone(out["windows"]["250"])

    def test_missing_subgroup_never_fills_with_zero(self):
        post, _ = transport(observations=rows(25, with_subgroups=False))
        out = c.fetch_one("252990", "20261016", "FAKE_TOKEN", post=post,
                          pause=lambda _: None)
        self.assertEqual(out["row_count"], 25)
        self.assertIsNone(out["windows"]["20"]["pension_etc_sum_shares"])
        self.assertIsNone(out["rows"][0]["pension_etc_net_shares"])

    def test_reject_partial_subject_coverage(self):
        data = rows(25)
        data[10].pop("penfnd_etc")
        post, _ = transport(observations=data)
        with self.assertRaisesRegex(c.KiwoomCollectionError, "partial_subgroup_coverage"):
            c.fetch_one("252990", "20261016", "FAKE_TOKEN",
                        post=post, pause=lambda _: None)

    def test_reject_duplicate_across_page_boundary(self):
        data = rows(201)
        data[100]["dt"] = data[99]["dt"]
        post, _ = transport(observations=data)
        with self.assertRaisesRegex(c.KiwoomCollectionError, "future_duplicate_or_unordered_day"):
            c.fetch_one("252990", "20261016", "FAKE_TOKEN",
                        post=post, pause=lambda _: None)

    def test_reject_decimal_quantity_and_future_day(self):
        for name, bad_value in (("orgn", "1.5"), ("frgnr_invsr", None),
                                ("penfnd_etc", 5), ("dt", "20261020")):
            data = rows(20)
            data[0][name] = bad_value
            post, _ = transport(observations=data)
            with self.subTest(name=name), self.assertRaises(c.KiwoomCollectionError):
                c.fetch_one("252990", "20261016", "FAKE_TOKEN",
                            post=post, pause=lambda _: None)

    def test_unauthorized_cursor_and_failure_are_fail_closed(self):
        post, _ = transport(observations=rows(201), broken_at=1)
        with self.assertRaisesRegex(c.KiwoomCollectionError, "future_duplicate_or_unordered_day"):
            c.fetch_one("252990", "20261016", "FAKE_TOKEN",
                        post=post, pause=lambda _: None)
        post, _ = transport(fail_code="252990")
        with self.assertRaisesRegex(c.KiwoomCollectionError, "flow_denied"):
            c.fetch_one("252990", "20261016", "FAKE_TOKEN",
                        post=post, pause=lambda _: None)

    def test_success_writes_only_to_private_directory_and_never_prints_quantities(self):
        post, _ = transport(observations=rows(100))
        with tempfile.TemporaryDirectory() as private:
            result = c.collect(codes=["252990"], asof="20261016",
                               app_key="FAKE_KEY", app_secret="FAKE_SECRET",
                               destination=Path(private) / "secured",
                               post=post, pause=lambda _: None)
            self.assertEqual(result["symbols_ok"], 1)
            self.assertEqual(result["symbols_failed"], 0)
            self.assertFalse(result["published"])
            blob = Path(private, "secured", "investor_20261016.json")
            snapshot = json.loads(blob.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["datasets"]["252990"]["row_count"], 100)
            self.assertIsNone(snapshot["datasets"]["252990"]["windows"]["120"])
            self.assertFalse(snapshot["confirmed_scoring_allowed"])
            self.assertNotIn("FAKE_KEY", blob.read_text())
            self.assertNotIn("FAKE_SECRET", blob.read_text())
            self.assertNotIn("DO_NOT_LOG_SYNTHETIC_TOKEN", blob.read_text())
            self.assertNotIn("SENSITIVE_REMOTE_VALUE", blob.read_text())
            if os.name == "posix":
                self.assertEqual(blob.stat().st_mode & 0o777, 0o600)

    def test_partial_failure_not_zero_and_no_server_echo(self):
        def fake(url, body, headers):
            if url.endswith("/oauth2/token"):
                return {"body": {"token": "FAKE_TOKEN"}}
            if body["stk_cd"] == "005930":
                return {"body": {"return_code": -12,
                                 "return_msg": "DO_NOT_LEAK_ACCOUNT_ID"}}
            return {"body": {"return_code": 0,
                             "stk_invsr_orgn": rows(21)},
                    "cont_yn": "N", "next_key": ""}
        with tempfile.TemporaryDirectory() as folder:
            out = c.collect(codes=["252990", "005930"], asof="20261016",
                            app_key="FAKE_KEY", app_secret="FAKE_SECRET",
                            destination=Path(folder), post=fake,
                            pause=lambda _: None)
            self.assertEqual(out["status"], "partial_failure")
            self.assertEqual(out["symbols_ok"], 1)
            self.assertEqual(out["symbols_failed"], 1)
            self.assertEqual(out["failure_categories"]["005930"], "flow_denied")
            self.assertNotIn("DO_NOT_LEAK_ACCOUNT_ID", json.dumps(out))
            data = json.loads((Path(folder) / "investor_20261016.json").read_text())
            self.assertNotIn("005930", data["datasets"])
            self.assertNotIn("DO_NOT_LEAK_ACCOUNT_ID", json.dumps(data))

    def test_complete_existing_private_snapshot_survives_failed_retry(self):
        def first(url, body, headers):
            if url.endswith("/oauth2/token"):
                return {"body": {"token": "FAKE"}}
            return {"body": {"return_code": 0, "stk_invsr_orgn": rows(22)},
                    "cont_yn": "N", "next_key": ""}
        def second(url, body, headers):
            if url.endswith("/oauth2/token"):
                return {"body": {"token": "FAKE"}}
            if body["stk_cd"] == "005930":
                return {"body": {"return_code": -1}}
            return {"body": {"return_code": 0, "stk_invsr_orgn": rows(22)},
                    "cont_yn": "N", "next_key": ""}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c.collect(codes=["252990"], asof="20261016",
                      app_key="FAKE", app_secret="FAKE", destination=root,
                      post=first, pause=lambda _: None)
            path = root / "investor_20261016.json"
            old = path.read_bytes()
            with self.assertRaisesRegex(c.KiwoomCollectionError, "existing_complete_snapshot_protected"):
                c.collect(codes=["252990", "005930"], asof="20261016",
                          app_key="FAKE", app_secret="FAKE", destination=root,
                          post=second, pause=lambda _: None)
            self.assertEqual(path.read_bytes(), old)

    def test_existing_watchlist_cannot_be_silently_dropped_by_retry(self):
        def fake(url, body, headers):
            if url.endswith("/oauth2/token"):
                return {"body": {"token": "FAKE"}}
            return {"body": {"return_code": 0, "stk_invsr_orgn": rows(20)},
                    "cont_yn": "N", "next_key": ""}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c.collect(codes=["252990", "005930"], asof="20261016",
                      app_key="FAKE", app_secret="FAKE", destination=root,
                      post=fake, pause=lambda _: None)
            with self.assertRaisesRegex(c.KiwoomCollectionError, "existing_watchlist_data_protected"):
                c.collect(codes=["252990"], asof="20261016",
                          app_key="FAKE", app_secret="FAKE", destination=root,
                          post=fake, pause=lambda _: None)

    def test_repo_public_path_is_forbidden_for_flow_storage(self):
        with self.assertRaisesRegex(c.KiwoomCollectionError, "outside_git_repo"):
            c._private_destination(c.HOST_ROOT / "docs" / "collected_kiwoom")

    def test_missing_credentials_stops_before_http(self):
        calls = []
        def no_calls(*args):
            calls.append(args)
            raise AssertionError("network must not be reached")
        with self.assertRaisesRegex(c.KiwoomCollectionError, "missing_credentials"):
            c._token("", "", no_calls)
        self.assertEqual(calls, [])

    def test_provider_token_failure_does_not_include_remote_text(self):
        def fake(url, body, headers):
            return {"body": {"return_code": -100,
                             "return_msg": "LEAKED_SECRET",
                             "token": "LEAKED_ACCESS_TOKEN"}}
        with self.assertRaises(c.KiwoomCollectionError) as caught:
            c._token("FAKE_KEY", "FAKE_SECRET", fake)
        self.assertEqual(str(caught.exception), "token_denied")


if __name__ == "__main__":
    unittest.main()
