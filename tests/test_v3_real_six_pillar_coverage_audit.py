"""Reconcile actual saved V3 factor coverage, not six certified categories."""
import copy
import json
from pathlib import Path
import unittest

import v3_real_six_pillar_coverage_audit as audit


def actual_inputs():
    d=Path(__file__).resolve().parent.parent/"docs"
    return [
        json.loads((d/name).read_text(encoding="utf8")) for name in (
            "v22-market-catalog.json",
            "v3-dart-bulk-history.json",
            "v3-dart-bulk-provisional.json",
            "v3-weekly-rotation.json")
    ]


class RealCoverageAuditTests(unittest.TestCase):
    def test_actual_saved_sources_are_reconciled_with_no_fake_100point(self):
        sources=actual_inputs()
        out=audit.coverage(*sources)
        self.assertEqual(out["listed_total"],sources[0]["listed_total"])
        self.assertEqual(out["verified_four_year_income_reference_issuers"],
                         sources[1]["four_year_income_observed_count"])
        self.assertEqual(out["historical_dart_distinct_issuer_count"],
                         len(sources[1]["records"]))
        self.assertEqual(out["six_pillar_official_complete_100pt_issuer_count"],0)
        self.assertEqual(out["official_investor_IAS_source_issuer_count"],0)
        self.assertEqual(out["six_pillar_numeric_provisional_issuer_counts"]["ias"],0)
        self.assertEqual(out["six_pillar_numeric_provisional_issuer_counts"]["industry"],0)
        # Four-year growth and three-year operating margin are independent
        # genuine evidence. Do not mistake their union for 4Y completeness.
        finance=out["six_pillar_numeric_provisional_issuer_counts"]["financial"]
        growth=sources[2]["companies_with_computable_4y_income_scores"]
        margin=sources[2]["companies_with_computable_3y_margin_scores"]
        self.assertEqual(finance,margin)
        self.assertGreaterEqual(finance,growth)
        self.assertEqual(
            out["three_year_margin_provisional_score_issuers"],margin)
        self.assertEqual(
            out["verified_four_year_income_reference_issuers"],growth)
        self.assertLessEqual(
            out["four_numerical_areas_same_date_overlap_count"],finance)
        self.assertEqual(out["six_pillar_numeric_provisional_issuer_counts"]["technical"],
                         sources[3]["numeric_technical_candidates_this_cycle"])
        self.assertFalse(out["buy_recommendations_authorized"])
        self.assertFalse(out["investment_grade_scores_confirmed"])
        overlap=out["four_numerical_areas_same_date_overlap_count"]
        self.assertLessEqual(overlap,
                             out["six_pillar_numeric_provisional_issuer_counts"]["financial"])
        self.assertLessEqual(len(out["four_numerical_areas_diagnostic_sample_codes"]),50)
        self.assertTrue(all(len(h)==64 for h in
                       out["source_sha256_canonical"].values()))
        self.assertEqual(out,audit.coverage(*sources))

    def test_stale_market_or_fake_full_pillars_fail_closed(self):
        base=actual_inputs()
        stale=copy.deepcopy(base)
        stale[3]["market_asof"]="2026-10-19"
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "incompatible_saved_source_status"):
            audit.coverage(*stale)
        inflated=copy.deepcopy(base)
        inflated[2]["fully_verified_six_pillar_count"]=1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "incompatible_saved_source_status"):
            audit.coverage(*inflated)
        inflated=copy.deepcopy(base)
        inflated[1]["four_year_income_observed_count"]+=1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "historical_coverage_claim_mismatch"):
            audit.coverage(*inflated)

    def test_unverified_financial_or_technical_data_cannot_borrow_peer_scores(self):
        base=actual_inputs()
        altered=copy.deepcopy(base)
        altered[3]["numeric_technical_candidates_this_cycle"]+=1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "technical_source_count_disagrees"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        a=altered[2]["rows"][0]
        a["confirmed_v3_score"]=77
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "untrusted_provisional_pillar_row"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        altered[1]["records"]["999999"]={"years":{}}
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "unexpected_historical_issuer"):
            audit.coverage(*altered)

if __name__=="__main__":
    unittest.main()
