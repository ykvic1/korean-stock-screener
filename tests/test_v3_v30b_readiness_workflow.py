"""Network-free constraints for the daily V3.0b release blocker watch."""
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
import unittest

from v3_historical_public_paths_audit import PublicPathAuditError, audit


ROOT=Path(__file__).resolve().parents[1]
WORKFLOW=ROOT/".github/workflows/v3-v30b-readiness-audit.yml"


class V30bReadinessWorkflowTests(unittest.TestCase):
    def test_primary_publication_dispatches_secondary_only_after_change(self):
        primary = (ROOT / ".github/workflows/v3-current-observed-scorecard.yml").read_text()
        secondary = (ROOT / ".github/workflows/v3-all-listed-scorecard.yml").read_text()
        block = primary.split("      - name: Refresh secondary CSV after verified primary publication", 1)[1].split("      - name:", 1)[0]
        self.assertIn("steps.publication.outputs.changed == 'true'", block)
        self.assertIn('gh workflow run v3-all-listed-scorecard.yml', block)
        self.assertIn('-f mode=write', block)
        self.assertNotIn('gh workflow run v3-current-observed-scorecard.yml', secondary)
        self.assertIn('group: korea-stock-screener-scorecard-publisher', secondary)

    def test_frozen_adjusted_path_evidence_retains_all_unresolved_pairs(self):
        import csv
        from collections import Counter
        from math import isfinite
        data = json.loads((ROOT / "ops/v30b-frozen-adjusted-path-evidence.json").read_text())
        with (ROOT / "ops/v3-performance-share-adjustments-20251113.csv").open() as stream:
            events = {(row["code"], row["listing_on"]) for row in csv.DictReader(stream)}
        rows = data["outcomes"]
        self.assertEqual(len(rows), data["event_count"])
        self.assertEqual({(row["code"], row["event_date"]) for row in rows}, events)
        total = Counter()
        unresolved = []
        for row in rows:
            path = row["year_path"]
            counts = path["adjacent_traded_session_pairs"]
            self.assertEqual(sum(counts.values()), path["raw_traded_sessions"] - 1)
            self.assertEqual(len(path["unresolved_pairs"]), counts.get("krx_reference_differs", 0))
            self.assertEqual(path["adjusted_positive_closes_on_traded_sessions"], path["raw_traded_sessions"])
            self.assertIn(row["official_daily_reference_factor_match"], ("exact", "within_rounding"))
            total.update(counts)
            for pair in path["unresolved_pairs"]:
                self.assertLess(pair["prior"], pair["current"])
                self.assertTrue(isfinite(pair["relative_gap_basis_points"]))
                self.assertIs(pair["official_scale_unchanged"], True)
                unresolved.append((row["code"], pair["prior"], pair["current"]))
        self.assertEqual(dict(total), data["adjacent_pair_counts"])
        self.assertEqual(len(set(unresolved)), len(unresolved))
        self.assertEqual(len(unresolved), 39)
        self.assertEqual({code for code, _, _ in unresolved}, {"291230"})
        self.assertFalse(data["official_adjusted_history_verified"])
        self.assertEqual(data["new_provider_calls"], 0)

    def test_daily_watch_is_read_only_and_independent(self):
        content=WORKFLOW.read_text(encoding="utf8")
        self.assertIn("cron: '23 0 * * *'",content)
        self.assertIn("contents: read",content)
        self.assertNotIn("contents: write",content)
        self.assertIn("workflow_dispatch:",content)
        self.assertIn("pull_request:",content)
        self.assertIn("GITHUB_STEP_SUMMARY",content)
        self.assertIn("if: always()",content)
        for forbidden in ("pip install","curl ","DART_API_KEY","KRX_API_KEY",
                          "git push","git commit","confirmed_score =",
                          "v30b_release_ready = true"):
            self.assertNotIn(forbidden,content)

    def test_historical_pilot_runs_without_release_authority(self):
        content=WORKFLOW.read_text(encoding="utf8")
        self.assertIn("python v3_historical_performance_pilot_audit.py >",content)
        self.assertIn("'ops/v3-performance-*-20251113.csv'",content)
        self.assertIn('pilot.get("performance_validated") is not False',content)
        self.assertIn('"scheduled_cash_entitlements"',content)
        self.assertIn('"paths_with_unpriced_sessions"',content)
        self.assertIn('"price_scenarios_with_reported_dividend_cash"',content)
        self.assertIn('"paths_with_dividend_entitlement_unpaid_or_unverified"',content)
        self.assertIn('"price_scenarios_with_fractional_cash_claim"', content)
        self.assertIn('"unpriced_final_distributions"', content)

    def test_public_paths_reconcile_to_historical_research_ledgers(self):
        report = audit(ROOT)
        self.assertFalse(report["performance_validated"])
        self.assertEqual(report["operating_cohort_count"], 2573)
        self.assertEqual(report["observed_entry_count"], 2476)
        self.assertEqual(report["delisting_paths_reconciled"], 85)
        self.assertEqual(report["reviewed_share_action_paths_reconciled"], 1242)
        self.assertEqual(report["financial_detail_issuers_reconciled"], 107)
        self.assertEqual(report["horizons"]["12"], {
            "priced": 1870, "entered_but_unpriced": 606, "downside_observed": 1870,
            "priced_paths_with_gaps": 175, "unpriced_paid_rights": 9,
        })
        content = WORKFLOW.read_text(encoding="utf8")
        self.assertIn("python v3_historical_public_paths_audit.py >", content)
        self.assertIn("v30b-historical-public-paths.json", content)
        self.assertIn("ops/v3-performance-market-share-action-replay-20251113.json", content)

    def test_public_paths_reject_drift_from_saved_ledgers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "ops").mkdir()
            for suffix in ("operating-scope", "market-coverage", "financial-factor-replay",
                           "market-share-action-replay", "price-replay", "downside-horizons"):
                extension = "csv" if suffix in ("price-replay", "downside-horizons") else "json"
                file = Path("ops") / f"v3-performance-{suffix}-20251113.{extension}"
                copyfile(ROOT / file, root / file)
            file = Path("docs/v3-historical-price-paths-20251113.json")
            feed = json.loads((ROOT / file).read_bytes())
            row = next(r for r in feed["rows"].values()
                       if r["paths"].get("1", [None] * 5)[3] == "delisting_case_replay"
                       and r["paths"]["1"][0] is not None)
            row["paths"]["1"][0] += 1
            (root / file).write_text(json.dumps(feed))
            with self.assertRaisesRegex(PublicPathAuditError, "published_delisting_path_differs_from_ledger"):
                audit(root)
            row["paths"]["1"][0] -= 1
            financial = json.loads((root / "ops/v3-performance-financial-factor-replay-20251113.json").read_bytes())
            paths = next(r["paths"] for code, r in feed["rows"].items() if code not in financial["rows"]
                         and r["paths"].get("12", [None] * 5)[3] == "reviewed_share_action")
            saved = paths["12"].copy()
            paths["12"][0] += 1
            (root / file).write_text(json.dumps(feed))
            with self.assertRaisesRegex(PublicPathAuditError, "published_share_action_path_differs_from_ledger"):
                audit(root)
            del paths["12"]
            (root / file).write_text(json.dumps(feed))
            with self.assertRaisesRegex(PublicPathAuditError, "reviewed_share_action_paths_missing_from_publication"):
                audit(root)
            paths["12"] = saved
            feed["unpriced_paid_rights"]["12"].pop()
            (root / file).write_text(json.dumps(feed))
            with self.assertRaisesRegex(PublicPathAuditError, "published_unpriced_paid_rights_mismatch"):
                audit(root)

    def test_main_audit_must_fail_closed_and_no_fake_release(self):
        content=WORKFLOW.read_text(encoding="utf8")
        self.assertIn("python v3_v30b_release_gate.py --assert-blocked",content)
        self.assertIn('if: ${{ github.event_name != \'pull_request\' }}',content)
        self.assertIn('set -euo pipefail',content)
        self.assertIn('v30b-readiness-aggregate.json',content)
        for required in (
            "public_summary_valid","public_issuer_csv_reconciled",
            "public_fcf_issuer_subscores_reconciled",
            "dart_historical_receipts_remaining_months",
            "historical_fixed_window_extracted","v30b_release_ready",
            "confirmed_scoring_authorized","investment_grade_certified_count",
            "blocker_codes","external_provider_calls",
            "r1d_operational_publication_reconciled",
            "r1d_100weight_observed_count",
            "r1d_same_market_day_and_roster",
        ):
            self.assertIn(required,content)
        self.assertNotIn("|| true",next(line for line in content.splitlines()
                                      if line.strip().startswith("python v3_v30b_release_gate.py")))


if __name__=="__main__":
    unittest.main()
