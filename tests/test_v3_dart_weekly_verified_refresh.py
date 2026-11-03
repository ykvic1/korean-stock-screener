"""Network-free OpenDART annual current-source rebind safety checks."""
import copy
from datetime import date
import json
from pathlib import Path
from unittest.mock import patch
import re
import tempfile
import unittest

import v3_dart_bulk_history as bulk
import v3_dart_weekly_freshness as freshness
import v3_dart_weekly_verified_refresh as mod
from test_v3_dart_bulk_history import FakeDART
from test_v3_dart_rolling_book_reference import DAY, fixture

KST_DAY = date(2026, 10, 20)
KEY = "synthetic-key-not-secret-123"


def evidence():
    market, catalog = fixture()
    source = FakeDART()
    history, count = bulk.collect(
        market, catalog, None, session=source, key=KEY, groups=1)
    assert count == 5
    book = {"asof": DAY, "market_listed_total": 5,
            "records": [], "observations": []}
    return market, catalog, book, history


def replay(archive, issuer):
    return {issuer: copy.deepcopy(archive["records"][issuer]["years"])}


class VerifiedAnnualRefreshTests(unittest.TestCase):
    def test_same_data_live_recheck_uses_five_calls_and_preserves_book(self):
        market, catalog, book, history = evidence()
        before = copy.deepcopy(history)
        caller = FakeDART()
        report, updated, points = mod.execute(
            market, catalog, book, history, when=KST_DAY, part=0,
            mode="live", key=KEY, session=caller)
        self.assertEqual(len(caller.calls), 5)
        self.assertEqual(report["source_api_calls"], 5)
        self.assertEqual(report["reconciled_company_count"], 0)
        self.assertFalse(report["source_archive_changed"])
        self.assertEqual(history, before)
        self.assertEqual(updated["records"], history["records"])
        self.assertEqual(points["asof"], DAY)

    def test_actual_newer_receipt_rebinds_only_verified_scope_year(self):
        market, catalog, book, history = evidence()
        issuer = freshness.slice_codes(history["records"], KST_DAY, 0)[1][0]
        observed = replay(history, issuer)
        orig = copy.deepcopy(observed[issuer]["2025"]["CFS"])
        amended = copy.deepcopy(orig)
        amended["receipt_id"] = "20260418000002"
        amended["values"]["revenue"] += 7000
        observed[issuer]["2025"]["CFS"] = amended
        report, updated, _ = mod.reconcile(
            market, catalog, book, history, observed,
            when=KST_DAY, part=0)
        self.assertEqual(report["reconciled_company_year_count"], 1)
        self.assertEqual(report["reconciled_company_count"], 1)
        self.assertEqual(report["market_asof"], DAY)
        self.assertTrue(report["source_archive_changed"])
        self.assertEqual(updated["records"][issuer]["years"]["2025"]["CFS"], amended)
        self.assertEqual(history["records"][issuer]["years"]["2025"]["CFS"], orig)
        self.assertFalse(report["historical_PIT_certified"])
        self.assertFalse(report["buy_recommendations_authorized"])

    def test_missing_year_is_not_zero_and_preserves_existing_data(self):
        market, catalog, book, history = evidence()
        issuer = freshness.slice_codes(history["records"], KST_DAY, 0)[1][0]
        observed = replay(history, issuer)
        observed[issuer]["2025"] = {}
        report, updated, _ = mod.reconcile(
            market, catalog, book, history, observed,
            when=KST_DAY, part=0)
        self.assertEqual(report["reconciled_company_year_count"], 0)
        self.assertEqual(report["reason_counts"]["now_missing_keep_previous"], 1)
        self.assertEqual(updated["records"][issuer]["years"]["2025"],
                         history["records"][issuer]["years"]["2025"])

    def test_same_receipt_drift_and_older_receipt_are_quarantined(self):
        market, catalog, book, history = evidence()
        issuer = freshness.slice_codes(history["records"], KST_DAY, 0)[1][0]
        for receipt, reason in (
            ("20260318000001", "same_receipt_data_changed_keep_previous"),
            ("20260218000001", "receipt_regression_keep_previous"),
            ("20271018000001", "untrusted_refresh_keep_previous")
        ):
            with self.subTest(receipt=receipt):
                observed = replay(history, issuer)
                amended = observed[issuer]["2025"]["CFS"]
                amended["receipt_id"] = receipt
                amended["values"]["revenue"] += 1
                report, updated, _ = mod.reconcile(
                    market, catalog, book, history, observed,
                    when=KST_DAY, part=0)
                self.assertEqual(report["reason_counts"][reason], 1)
                self.assertFalse(report["source_archive_changed"])
                self.assertEqual(updated["records"], history["records"])

    def test_absent_mapping_is_quarantined_for_that_ticker_only(self):
        market, catalog, book, history = evidence()
        issuer = freshness.slice_codes(history["records"], KST_DAY, 0)[1][0]
        observed = replay(history, issuer)
        report, updated, _ = mod.reconcile(
            market, catalog, book, history, observed,
            when=KST_DAY, part=0, unmapped={issuer})
        self.assertEqual(report["reason_counts"]["unmapped_keep_previous"], 4)
        self.assertFalse(report["source_archive_changed"])
        self.assertEqual(updated["records"], history["records"])

    def test_fail_closed_before_source_call_when_book_misaligned(self):
        market, catalog, book, history = evidence()
        out_of_date = copy.deepcopy(book)
        out_of_date["asof"] = "2026-10-18"
        caller = FakeDART()
        report, updated, score = mod.execute(
            market, catalog, out_of_date, history, when=KST_DAY, part=0,
            mode="live", key=KEY, session=caller)
        self.assertEqual(report["status"], "waiting_aligned_annual_book")
        self.assertEqual(report["source_api_calls"], 0)
        self.assertEqual(caller.calls, [])
        self.assertIsNone(updated)
        self.assertIsNone(score)
        with self.assertRaisesRegex(mod.RebindError, "selection_mismatch"):
            issuer = freshness.slice_codes(history["records"], KST_DAY, 0)[1][0]
            mod.reconcile(market, catalog, book, history, replay(history, issuer),
                          when=KST_DAY, part=0, selected=[])

    def test_stale_annual_scores_defer_without_api_and_verify_stays_strict(self):
        market, catalog, book, history = evidence()
        current = bulk.research_parts(history, catalog, book)
        stale = copy.deepcopy(current)
        stale["companies_with_computable_pb_scores"] = -1
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            sources = (market, catalog, book, history, stale)
            paths = [base / f"annual_{i}.json" for i in range(len(sources))]
            for path, source in zip(paths, sources):
                path.write_text(json.dumps(source), encoding="utf8")
            report_path = base / "report.json"
            common = [
                "--run-day", KST_DAY.isoformat(), "--part", "1",
                "--market", str(paths[0]), "--catalog", str(paths[1]),
                "--book", str(paths[2]), "--history", str(paths[3]),
                "--scores", str(paths[4]), "--report", str(report_path),
            ]
            self.assertEqual(mod.main(["--mode", "publish", *common]), 0)
            report = json.loads(report_path.read_text(encoding="utf8"))
            self.assertEqual(report["status"], "waiting_annual_score_replay")
            self.assertEqual(report["source_api_calls"], 0)
            self.assertFalse(report["source_archive_changed"])
            self.assertFalse(report["historical_PIT_certified"])
            self.assertEqual(json.loads(paths[3].read_text(encoding="utf8")), history)
            self.assertEqual(json.loads(paths[4].read_text(encoding="utf8")), stale)
            with self.assertRaisesRegex(mod.RebindError,
                                        "existing_annual_source_and_scores_disagree"):
                mod.main(["--mode", "verify", *common])

    def test_rebind_slots_follow_annual_book_then_hourly_score_replay(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/v3-dart-weekly-verified-refresh.yml").read_text(encoding="utf8")
        bulk_flow = (root / ".github/workflows/v3-dart-bulk-four-year.yml").read_text(encoding="utf8")
        crons = re.findall(r"^\s+- cron: '([^']+)'", workflow, flags=re.M)
        self.assertEqual(crons, [
            "11 18 * * *", "11 21 * * *", "11 0 * * *", "11 3 * * *",
        ])
        self.assertIn("cron: '29 * * * *'", bulk_flow)
        self.assertIn("git pull --rebase origin main", workflow)

    def test_verified_same_market_day_company_cache_skips_redundant_master(self):
        market, catalog, book, history = evidence()
        source = FakeDART()
        # Source must first pass the existing ZIP schema and exact-census
        # identity checks; an arbitrary code->corporation dictionary is not enough.
        master = source.get("https://opendart.fss.or.kr/api/corpCode.xml",
                            {"crtfc_key": KEY}, (20, 95))
        mapping = bulk._mapping(master.content, set(history["records"]))
        cache = bulk.make_company_mapping_cache(
            mapping, DAY, set(history["records"]))
        provider = FakeDART()
        report, updated, _ = mod.execute(
            market, catalog, book, history, when=KST_DAY, part=0,
            mode="live", key=KEY, session=provider, company_cache=cache)
        self.assertEqual(report["source_api_calls"], 4)
        self.assertEqual(report["company_master_source_api_calls"], 0)
        self.assertTrue(report["company_master_cache_used"])
        self.assertEqual(len(provider.calls), 4)
        self.assertTrue(all(not uri.endswith("corpCode.xml")
                            for uri, _ in provider.calls))
        self.assertEqual(updated["records"], history["records"])

    def test_corrupt_same_day_mapping_cache_fails_before_any_source_call(self):
        market, catalog, book, history = evidence()
        master = FakeDART().get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            {"crtfc_key": KEY}, (20, 95))
        mapping = bulk._mapping(master.content, set(history["records"]))
        cache = bulk.make_company_mapping_cache(
            mapping, DAY, set(history["records"]))
        tampered = copy.deepcopy(cache)
        tampered["issuer_codes"][next(iter(mapping))] = "99999999"
        provider = FakeDART()
        with self.assertRaisesRegex(
                mod.RebindError, "untrusted_official_company_mapping_cache"):
            mod.execute(market, catalog, book, history, when=KST_DAY,
                        part=0, mode="live", key=KEY,
                        session=provider, company_cache=tampered)
        self.assertEqual(provider.calls, [])

    def test_single_company_master_connection_failure_gets_bounded_retry(self):
        market, catalog, book, history = evidence()

        class Once(FakeDART):
            def __init__(self):
                super().__init__()
                self.fail_once = True

            def get(self, url, params, timeout):
                if url.endswith("corpCode.xml") and self.fail_once:
                    self.fail_once = False
                    self.calls.append((url, params))
                    raise ConnectionError("secret-never-print")
                return super().get(url, params, timeout)

        provider = Once()
        with patch.object(mod.time, "sleep", return_value=None):
            report, updated, _ = mod.execute(
                market, catalog, book, history, when=KST_DAY, part=0,
                mode="live", key=KEY, session=provider)
        self.assertEqual(report["source_api_calls"], 6)
        self.assertEqual(report["company_master_source_api_calls"], 2)
        self.assertFalse(report["company_master_cache_used"])
        self.assertEqual(len(provider.calls), 6)
        self.assertEqual(updated["records"], history["records"])

    def test_persistent_mapping_outage_preserves_all_prior_score_references(self):
        market, catalog, book, history = evidence()
        before = copy.deepcopy(history)

        class Offline(FakeDART):
            def get(self, url, params, timeout):
                self.calls.append((url, params))
                raise ConnectionError("hidden URL and token")

        provider = Offline()
        with patch.object(mod.time, "sleep", return_value=None):
            report, updated, score = mod.execute(
                market, catalog, book, history, when=KST_DAY, part=0,
                mode="publish", key=KEY, session=provider)
        self.assertEqual(report["status"],
                         "waiting_official_company_master_transport")
        self.assertEqual(report["source_api_calls"], 2)
        self.assertFalse(report["source_archive_changed"])
        self.assertFalse(report["historical_PIT_certified"])
        self.assertFalse(report["buy_recommendations_authorized"])
        self.assertEqual(report["review_flags"],
                         [{"reason": "waiting_official_company_master_transport"}])
        self.assertEqual(len(provider.calls), 2)
        self.assertIsNone(updated)
        self.assertIsNone(score)
        self.assertEqual(history, before)
        self.assertNotIn("hidden", json.dumps(report))

    def test_invalid_zip_still_fails_closed_without_provisional_override(self):
        market, catalog, book, history = evidence()

        class BadZip(FakeDART):
            def get(self, url, params, timeout):
                if url.endswith("corpCode.xml"):
                    self.calls.append((url, params))
                    class Response:
                        content = b"not-a-valid-zip"
                        def raise_for_status(self):
                            return None
                    return Response()
                return super().get(url, params, timeout)

        provider = BadZip()
        with self.assertRaisesRegex(
                mod.RebindError, "untrusted_official_company_master"):
            mod.execute(market, catalog, book, history, when=KST_DAY,
                        part=0, mode="publish", key=KEY, session=provider)
        self.assertEqual(len(provider.calls), 1)

    def test_rate_limit_defers_without_retry_and_auth_error_is_not_hidden(self):
        market, catalog, book, history = evidence()

        class HTTPError(Exception):
            pass

        class Status(FakeDART):
            def __init__(self, code):
                super().__init__()
                self.code = code
            def get(self, url, params, timeout):
                self.calls.append((url, params))
                error = HTTPError("private response")
                error.response = type("Response", (), {"status_code": self.code})()
                raise error

        limited = Status(429)
        report, updated, score = mod.execute(
            market, catalog, book, history, when=KST_DAY, part=0,
            mode="publish", key=KEY, session=limited)
        self.assertEqual(report["status"],
                         "waiting_official_company_master_rate_limit")
        self.assertEqual(report["source_api_calls"], 1)
        self.assertIsNone(updated)
        self.assertIsNone(score)
        forbidden = Status(403)
        with self.assertRaisesRegex(
                mod.RebindError, "official_company_master_auth_or_http_error"):
            mod.execute(market, catalog, book, history, when=KST_DAY,
                        part=0, mode="publish", key=KEY, session=forbidden)
        self.assertEqual(len(forbidden.calls), 1)

    def test_company_cache_rebind_key_matches_existing_four_year_collector(self):
        root = Path(__file__).resolve().parents[1]
        rebind = (root / ".github/workflows/v3-dart-weekly-verified-refresh.yml"
                  ).read_text(encoding="utf8")
        collector = (root / ".github/workflows/v3-dart-bulk-four-year.yml"
                     ).read_text(encoding="utf8")
        key = "v3-dart-corp-map-v1-${{ hashFiles('docs/v22-market-catalog.json') }}"
        self.assertIn(key, rebind)
        self.assertIn(key, collector)
        self.assertIn("actions/cache/restore@v4", rebind)
        self.assertIn("path: .cache/v3-dart-company-codes.json", rebind)
        self.assertIn("cancel-in-progress: false", rebind)

    def test_audit_mode_never_uses_a_provider_or_writes_market(self):
        market, catalog, book, history = evidence()
        caller = FakeDART()
        report, updated, points = mod.execute(
            market, catalog, book, history, when=KST_DAY, part=0,
            mode="audit", key=KEY, session=caller)
        self.assertEqual(report["status"], "audit_plan_only")
        self.assertEqual(report["source_api_calls"], 0)
        self.assertEqual(caller.calls, [])
        self.assertIsNone(updated)
        self.assertIsNone(points)


if __name__ == "__main__":
    unittest.main()
