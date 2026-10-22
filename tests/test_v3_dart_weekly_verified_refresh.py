"""Network-free OpenDART annual current-source rebind safety checks."""
import copy
from datetime import date
import json
from pathlib import Path
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
        crons = re.findall(r"^\\s+- cron: '([^']+)'", workflow, flags=re.M)
        self.assertEqual(crons, [
            "11 18 * * *", "11 21 * * *", "11 0 * * *", "11 3 * * *",
        ])
        self.assertIn("cron: '29 * * * *'", bulk_flow)
        self.assertIn("git pull --rebase origin main", workflow)

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
