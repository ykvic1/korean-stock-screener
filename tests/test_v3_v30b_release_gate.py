"""V3.0b five-scored-area release audit must remain research-only."""
from __future__ import annotations

import copy
import csv
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from v3_v30b_release_gate import (assess_v30b, main, AREA_WEIGHTS,
                                    issuer_csv_reconciles,
                                    issuer_catalog_reconciles,
                                    issuer_fcf_preview_reconciles)

ROOT = Path(__file__).resolve().parents[1]


def public_inputs():
    return tuple(json.loads((ROOT / name).read_text(encoding="utf-8"))
                 for name in (
                     "docs/v3-current-observed-all-listed-summary.json",
                     "docs/v3-all-listed-scorecard-summary.json",
                     "docs/v3-krx-derived-backfill-progress.json"))


class V30bReadinessTests(unittest.TestCase):
    def test_current_public_sources_are_consistent_but_not_certified(self):
        observed, scorecard, backfill = public_inputs()
        r = assess_v30b(observed, scorecard, backfill)
        self.assertTrue(r["public_summary_valid"], r["blocker_codes"])
        self.assertEqual(r["listed_total"], observed["listed_total"])
        self.assertEqual(r["asof"], observed["asof"])
        self.assertEqual(r["observed_all_five_areas_100weight_count"],
                         observed["provisional_complete_100point_observation_count"])
        self.assertEqual(r["missing_all_five_area_observation_count"],
                         observed["listed_total"] -
                         observed["provisional_complete_100point_observation_count"])
        self.assertEqual(r["complete_area_issuer_counts"],
                         observed["complete_area_issuer_counts"])
        self.assertEqual(r["area_completion_gaps"],
                         {key: observed["listed_total"] - value
                          for key, value in observed["complete_area_issuer_counts"].items()})
        self.assertTrue(r["gates"]["fixed_window_krx_extraction"])
        self.assertFalse(r["historical_window_is_pit_or_adjusted_certified"])
        self.assertFalse(r["v30b_release_ready"])
        self.assertFalse(r["confirmed_scoring_authorized"])
        self.assertIsNone(r["certified_rank"])
        self.assertIsNone(r["certified_score"])
        self.assertEqual(r["investment_grade_certified_count"], 0)
        self.assertEqual(r["external_provider_calls"], 0)
        self.assertTrue(r["read_only"])

    def test_real_issuer_csv_replays_every_five_area_histogram(self):
        o, s, h = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        self.assertTrue(issuer_csv_reconciles(o, s, raw))
        report = assess_v30b(o, s, h, observed_csv_text=raw)
        self.assertTrue(report["public_summary_valid"], report["blocker_codes"])
        self.assertTrue(report["public_issuer_csv_reconciled"])
        self.assertTrue(report["gates"]["published_issuer_csv_coherence"])
        self.assertFalse(report["confirmed_scoring_authorized"])

    def test_exact_per_issuer_fcf_candidate_and_receipt_joins(self):
        o, s, h = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        preview = json.loads((ROOT / "docs/v3-dart-fcf-research-preview.json")
                             .read_text(encoding="utf-8"))
        gap = json.loads((ROOT / "docs/v3-dart-cashflow-gap-summary.json")
                         .read_text(encoding="utf-8"))
        self.assertTrue(issuer_fcf_preview_reconciles(o, raw, preview))
        baseline = assess_v30b(o, s, h, observed_csv_text=raw,
                               fcf_preview=preview, fcf_cashflow_gap=gap)
        self.assertTrue(baseline["public_summary_valid"], baseline["blocker_codes"])
        self.assertTrue(baseline["public_fcf_issuer_subscores_reconciled"])
        self.assertTrue(baseline["public_fcf_receipt_source_reconciled"])
        self.assertTrue(baseline["gates"]["published_fcf_issuer_subscore_source"])
        self.assertFalse(baseline["v30b_release_ready"])
        self.assertEqual(baseline["investment_grade_certified_count"], 0)

        # Change a plausible score for ONE stock. Aggregate counts, bounds,
        # and the original five-area CSV replay can still pass.
        rows = list(csv.reader(StringIO(raw)))
        col = {name: i for i, name in enumerate(rows[0])}
        for row in rows[1:]:
            if (row[col["FCF수익률_잠정점수"]]
                    and float(row[col["FCF수익률_잠정점수"]]) > 0):
                row[col["FCF수익률_잠정점수"]] = "0"
                break
        else:
            self.fail("expected at least one observed positive FCF subscore")
        out = StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)
        substituted = out.getvalue()
        self.assertTrue(issuer_csv_reconciles(o, s, substituted))
        self.assertFalse(issuer_fcf_preview_reconciles(o, substituted, preview))
        report = assess_v30b(o, s, h, observed_csv_text=substituted,
                             fcf_preview=preview, fcf_cashflow_gap=gap)
        self.assertIn("public_fcf_issuer_subscore_source_mismatch",
                      report["blocker_codes"])
        self.assertFalse(report["public_summary_valid"])
        self.assertFalse(report["gates"]["published_fcf_issuer_subscore_source"])
        self.assertFalse(report["confirmed_scoring_authorized"])

        # A plausible-looking modified preview is not source proof. The
        # individual match must detect it even if count/fingerprint still match.
        altered = copy.deepcopy(preview)
        for record in altered["rows"]:
            value = record["valuation_fcf_yield_candidate_points"]
            if value is not None and value < 4:
                record["valuation_fcf_yield_candidate_points"] = value + 0.000001
                break
        else:
            self.fail("expected a preview candidate below four points")
        self.assertFalse(issuer_fcf_preview_reconciles(o, raw, altered))
        report = assess_v30b(o, s, h, observed_csv_text=raw,
                             fcf_preview=altered, fcf_cashflow_gap=gap)
        self.assertTrue(report["public_fcf_receipt_source_reconciled"])
        self.assertIn("public_fcf_issuer_subscore_source_mismatch",
                      report["blocker_codes"])

        # A conflicting cashflow snapshot hash must invalidate a matching
        # CSV/preview pair; provenance consistency is a separate requirement.
        changed_gap = copy.deepcopy(gap)
        changed_gap["source_cashflow_sha256"] = "0" * 64
        report = assess_v30b(o, s, h, observed_csv_text=raw,
                             fcf_preview=preview, fcf_cashflow_gap=changed_gap)
        self.assertTrue(report["public_fcf_issuer_subscores_reconciled"])
        self.assertFalse(report["public_fcf_receipt_source_reconciled"])
        self.assertIn("public_fcf_receipt_source_aggregate_mismatch",
                      report["blocker_codes"])
        self.assertFalse(report["gates"]["published_fcf_issuer_subscore_source"])

    def test_catalog_census_exactly_matches_all_real_public_issuer_rows(self):
        o, s, h = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        catalog = json.loads((ROOT / "docs/v22-market-catalog.json").read_text(
            encoding="utf-8"))
        self.assertTrue(issuer_catalog_reconciles(o, catalog, raw))
        audit = assess_v30b(o, s, h, observed_csv_text=raw, catalog=catalog)
        self.assertTrue(audit["public_issuer_catalog_reconciled"])
        self.assertTrue(audit["gates"]["published_issuer_catalog_membership"])
        self.assertTrue(audit["public_summary_valid"])
        self.assertEqual(audit["investment_grade_certified_count"], 0)

    def test_substituted_or_misnamed_issuer_rejected_even_when_weights_match(self):
        o, s, h = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        catalog = json.loads((ROOT / "docs/v22-market-catalog.json").read_text(
            encoding="utf-8"))
        original = list(csv.reader(StringIO(raw)))
        col = {name: i for i, name in enumerate(original[0])}
        for field, value in (
                ("종목코드", "999999"),
                ("종목명", "잘못 연결된 기업"),
                ("시장", "KOSDAQ" if original[1][col["시장"]] == "KOSPI"
                              else "KOSPI")):
            with self.subTest(field=field):
                rows = copy.deepcopy(original)
                rows[1][col[field]] = value
                out = StringIO()
                csv.writer(out, lineterminator="\n").writerows(rows)
                self.assertFalse(
                    issuer_catalog_reconciles(o, catalog, out.getvalue()))
                audit = assess_v30b(o, s, h, observed_csv_text=out.getvalue(),
                                    catalog=catalog)
                self.assertFalse(audit["public_issuer_catalog_reconciled"])
                self.assertIn("public_issuer_catalog_membership_mismatch",
                              audit["blocker_codes"])
                self.assertFalse(audit["v30b_release_ready"])
                self.assertNotIn(rows[1][col["종목명"]],
                                 json.dumps(audit, ensure_ascii=False))
        altered = copy.deepcopy(catalog)
        altered["rows"][1]["code"] = altered["rows"][0]["code"]
        self.assertFalse(issuer_catalog_reconciles(o, altered, raw))
        altered = copy.deepcopy(catalog)
        altered["date"] = "2026-10-01"
        self.assertFalse(issuer_catalog_reconciles(o, altered, raw))
        altered = copy.deepcopy(catalog)
        altered["rows"].pop()
        self.assertFalse(issuer_catalog_reconciles(o, altered, raw))

    def test_corrupt_issuer_rows_never_reconcile_or_leak_tickers(self):
        o, s, h = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        original = list(csv.reader(StringIO(raw)))
        col = {name: i for i, name in enumerate(original[0])}
        for field, value in (
                ("산출가능배점_100점중", "99"),
                ("가격자료기준일", "2026-02-31"),
                ("정식투자점수", "100"),
                ("투자등급", "확정 투자등급"),
                ("거래량_OBV_잠정점수", "500"),
                ("시장", "UNKNOWN")):
            with self.subTest(field=field):
                rows = copy.deepcopy(original)
                rows[1][col[field]] = value
                out = StringIO()
                csv.writer(out, lineterminator="\n").writerows(rows)
                result = assess_v30b(o, s, h, observed_csv_text=out.getvalue())
                self.assertFalse(result["public_summary_valid"])
                self.assertFalse(result["public_issuer_csv_reconciled"])
                self.assertIn("public_issuer_csv_aggregate_mismatch",
                              result["blocker_codes"])
                self.assertFalse(result["v30b_release_ready"])
                self.assertNotIn(rows[1][0], json.dumps(result))
        rows = copy.deepcopy(original)
        rows[2][col["종목코드"]] = rows[1][col["종목코드"]]
        out = StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)
        self.assertFalse(issuer_csv_reconciles(o, s, out.getvalue()))
        self.assertFalse(issuer_csv_reconciles(o, s, raw + "EXTRA,ROW\n"))
        self.assertFalse(issuer_csv_reconciles(o, s, 123))

    def test_fcf_csv_subscores_cannot_bypass_five_area_reconciliation(self):
        observed, scorecard, backfill = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        rows = list(csv.reader(StringIO(raw)))
        columns = {name: index for index, name in enumerate(rows[0])}
        self.assertTrue(issuer_csv_reconciles(observed, scorecard, raw))

        for field, max_points in (
                ("FCF자산3년_잠정점수", 4),
                ("FCF수익률_잠정점수", 5)):
            index = columns[field]
            source_row = next(
                i for i, row in enumerate(rows[1:], 1) if row[index] != "")
            for altered_value in ("", str(max_points + 1), "-1", "NaN"):
                with self.subTest(field=field, altered=altered_value):
                    changed = copy.deepcopy(rows)
                    changed[source_row][index] = altered_value
                    sink = StringIO()
                    csv.writer(sink, lineterminator="\n").writerows(changed)
                    forged = sink.getvalue()
                    self.assertFalse(
                        issuer_csv_reconciles(observed, scorecard, forged))
                    audit = assess_v30b(
                        observed, scorecard, backfill,
                        observed_csv_text=forged)
                    self.assertIn(
                        "public_issuer_csv_aggregate_mismatch",
                        audit["blocker_codes"])
                    self.assertFalse(audit["v30b_release_ready"])
                    self.assertEqual(audit["investment_grade_certified_count"], 0)

        # Even a forged summary count cannot reclassify the CSV as certified.
        changed_summary = copy.deepcopy(observed)
        changed_summary["fcf_observed_issuer_counts"]["financial4"] += 1
        self.assertFalse(issuer_csv_reconciles(
            changed_summary, scorecard, raw))

    def test_no_old_ias_or_sector_as_sixth_scoring_area(self):
        self.assertEqual(AREA_WEIGHTS, {
            "volume": 20, "technical": 25, "financial": 20,
            "valuation": 20, "momentum": 15})
        o, s, h = public_inputs()
        s["weights"]["investor_IAS"] = 20
        r = assess_v30b(o, s, h)
        self.assertFalse(r["public_summary_valid"])
        self.assertIn("five_area_model_mismatch", r["blocker_codes"])
        self.assertFalse(r["gates"]["investment_grade_scoring"])

    def test_missing_values_never_become_zero_scores(self):
        o, s, h = public_inputs()
        o["missing_weight_is_not_zero"] = False
        r = assess_v30b(o, s, h)
        self.assertFalse(r["public_summary_valid"])
        self.assertIn("observed_only_semantics_mismatch", r["blocker_codes"])
        self.assertFalse(r["missing_factor_as_zero"])
        self.assertIsNone(r["certified_score"])

    def test_mismatched_count_and_histogram_fail_closed(self):
        o, s, h = public_inputs()
        variants = [
            lambda a, b, c: a.update(listed_total=a["listed_total"] + 1),
            lambda a, b, c: b.update(asof="2026-10-01"),
            lambda a, b, c: a.update(
                provisional_complete_100point_observation_count=1),
            lambda a, b, c: a["area_available_weight_distribution"]
                ["financial"].update({"20": 1}),
            lambda a, b, c: a["available_weight_distribution_by_market"]
                ["KOSPI"].update({"100": 0}),
            lambda a, b, c: c.update(five_session_cohorts_completed=1),
            lambda a, b, c: b.update(full_six_pillar_certified_count=1),
            lambda a, b, c: a["available_weight_distribution"].update({"101": 1}),
        ]
        for mutate in variants:
            aa, bb, cc = copy.deepcopy(o), copy.deepcopy(s), copy.deepcopy(h)
            mutate(aa, bb, cc)
            with self.subTest(mutate=mutate):
                r = assess_v30b(aa, bb, cc)
                self.assertFalse(r["public_summary_valid"])
                self.assertIsNone(r["observed_all_five_areas_100weight_count"])
                self.assertFalse(r["v30b_release_ready"])
                self.assertFalse(r["gates"]["investment_grade_scoring"])

    def test_fcf_only_secondary_scorecard_lag_is_bounded_not_certified(self):
        observed, scorecard, backfill = public_inputs()
        raw = (ROOT / "docs/v3-current-observed-all-listed.csv").read_text(
            encoding="utf-8-sig")
        self.assertTrue(issuer_csv_reconciles(observed, scorecard, raw))
        self.assertTrue(assess_v30b(observed, scorecard, backfill)[
            "public_summary_valid"])
        # A same-day older scorecard can omit legitimately source-replayed FCF
        # names; this is not 100-point investment score certification.
        self.assertFalse(assess_v30b(observed, scorecard, backfill)[
            "v30b_release_ready"])
        for area, value in (
                ("valuation", observed["observed_issuer_counts"]["valuation"] + 1),
                ("technical", max(0, observed["observed_issuer_counts"]["technical"] - 1)),
                ("financial", -1)):
            with self.subTest(area=area):
                damaged = copy.deepcopy(scorecard)
                damaged["provisional_numeric_issuer_counts"][area] = value
                self.assertFalse(issuer_csv_reconciles(observed, damaged, raw))
                report = assess_v30b(observed, damaged, backfill)
                self.assertFalse(report["public_summary_valid"])
                self.assertIn("area_count_or_source_mismatch", report["blocker_codes"])

    def test_an_untrusted_proof_cannot_enable_authorization(self):
        o, s, h = public_inputs()
        r = assess_v30b(o, s, h, proof={
            "verified": True, "signed": True, "v30b_release_ready": True,
            "certified_score": 100, "investment_grade_certified_count": 2497})
        self.assertTrue(r["public_summary_valid"])
        self.assertFalse(r["v30b_release_ready"])
        self.assertFalse(r["confirmed_scoring_authorized"])
        self.assertIsNone(r["certified_score"])
        self.assertIn("authenticated_source_chain_unverified", r["blocker_codes"])

    def test_invalid_payloads_fail_closed_and_only_codes_returned(self):
        r = assess_v30b({"private_issuer_id": "000001"}, None, [])
        self.assertFalse(r["public_summary_valid"])
        self.assertIsNone(r["listed_total"])
        self.assertNotIn("000001", json.dumps(r))
        self.assertIsNone(r["certified_rank"])

    def test_dart_receipt_checkpoint_is_counted_but_never_certified(self):
        o, s, h = public_inputs()
        receipt = json.loads((ROOT / "docs/v3-dart-historical-receipt-progress.json")
                             .read_text(encoding="utf-8"))
        report = assess_v30b(o, s, h, dart_receipt_progress=receipt)
        self.assertTrue(report["public_summary_valid"], report["blocker_codes"])
        self.assertTrue(report["dart_historical_receipt_checkpoint_valid"])
        self.assertTrue(report["gates"]["dart_receipt_checkpoint_coherence"])
        self.assertEqual(report["dart_historical_receipts_completed_months"],
                         receipt["filing_months_checkpointed"])
        self.assertEqual(report["dart_historical_receipts_remaining_months"],
                         receipt["filing_months_remaining"])
        self.assertFalse(report["gates"]["dart_120month_receipt_collection_complete"])
        self.assertIn("dart_historical_receipts_incomplete", report["blocker_codes"])
        self.assertFalse(report["dart_receipt_collection_is_pit_or_adjusted_certified"])
        self.assertIsNone(report["certified_score"])

    def test_dart_receipt_tampering_fails_closed_without_private_issuer_leak(self):
        o, s, h = public_inputs()
        original = json.loads((ROOT / "docs/v3-dart-historical-receipt-progress.json")
                              .read_text(encoding="utf-8"))
        variants = (
            ("filing_months_remaining", 0),
            ("filing_months_checkpointed", True),
            ("filing_months_checkpointed_ratio", 0.99999),
            ("last_checkpointed_filing_month", "2026-09"),
            ("current_collection_filing_month", "2018-02"),
            ("filing_category_scope", ["B", "C"]),
            ("private_receipt_pages_checkpointed", 999999),
            ("private_tail_digest_and_schema_verified", False),
            ("public_issuer_receipts_or_source_raw_published", True),
            ("historical_adjusted_prices_certified", True),
            ("historical_issuer_universe_pit_certified", True),
            ("v3_trade_scores_promoted", True),
        )
        for key, value in variants:
            with self.subTest(key=key):
                altered = copy.deepcopy(original)
                altered[key] = value
                altered["private_issuer_id"] = "SECRET-ISSUER-NAME"
                audit = assess_v30b(o, s, h, dart_receipt_progress=altered)
                self.assertFalse(audit["public_summary_valid"])
                self.assertFalse(audit["dart_historical_receipt_checkpoint_valid"])
                self.assertIn("invalid_dart_historical_receipt_checkpoint",
                              audit["blocker_codes"])
                self.assertFalse(audit["confirmed_scoring_authorized"])
                self.assertIsNone(audit["certified_score"])
                self.assertNotIn("SECRET-ISSUER-NAME", json.dumps(audit))

    def test_even_120_complete_filing_months_is_not_independent_pit_proof(self):
        o, s, h = public_inputs()
        receipt = json.loads((ROOT / "docs/v3-dart-historical-receipt-progress.json")
                             .read_text(encoding="utf-8"))
        receipt.update(filing_months_checkpointed=120,
                       filing_months_remaining=0,
                       filing_months_checkpointed_ratio=1.0,
                       last_checkpointed_filing_month="2026-09",
                       current_collection_filing_month=None)
        report = assess_v30b(o, s, h, dart_receipt_progress=receipt)
        self.assertTrue(report["public_summary_valid"], report["blocker_codes"])
        self.assertTrue(report["gates"]["dart_120month_receipt_collection_complete"])
        self.assertFalse(report["gates"]["authenticated_point_in_time_source_chain"])
        self.assertFalse(report["dart_receipt_collection_is_pit_or_adjusted_certified"])
        self.assertFalse(report["v30b_release_ready"])
        self.assertEqual(report["investment_grade_certified_count"], 0)

    def test_cli_default_is_read_only_and_assert_blocked_succeeds(self):
        with redirect_stdout(StringIO()) as buf:
            r = main(["--assert-blocked"])
        displayed = json.loads(buf.getvalue())
        self.assertEqual(displayed["schema"], "v3-v30b-five-area-readiness-audit-0.1")
        self.assertTrue(displayed["public_summary_valid"])
        self.assertTrue(displayed["public_issuer_csv_reconciled"])
        self.assertTrue(displayed["public_fcf_issuer_subscores_reconciled"])
        self.assertTrue(displayed["public_fcf_receipt_source_reconciled"])
        self.assertTrue(displayed["gates"]["published_fcf_issuer_subscore_source"])
        self.assertEqual(displayed, r)
        self.assertFalse(displayed["v30b_release_ready"])


if __name__ == "__main__":
    unittest.main()
