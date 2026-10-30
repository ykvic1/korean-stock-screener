"""Reconcile actual saved V3 factor coverage, not six certified categories."""
import copy
import json
from pathlib import Path
import unittest

import v3_real_six_pillar_coverage_audit as audit
from v3_dart_bulk_history import research_parts


def actual_inputs():
    d=Path(__file__).resolve().parent.parent/"docs"
    return [
        json.loads((d/name).read_text(encoding="utf8")) for name in (
            "v22-market-catalog.json",
            "v3-dart-bulk-history.json",
            "v3-dart-bulk-provisional.json",
            "v3-weekly-rotation.json")
    ]


class PublicationAlignmentTests(unittest.TestCase):
    def sources(self):
        catalog, history, parts, weekly = actual_inputs()
        # Fixture source content is real, but the alignment gate itself only
        # needs the authenticated headers and does not alter any source rows.
        history = {**history, "asof": catalog["date"],
                   "listed_total": catalog["listed_total"]}
        parts = {**parts, "asof": catalog["date"],
                 "listed_total": catalog["listed_total"]}
        weekly = {**weekly, "market_asof": catalog["date"],
                  "listed_total": catalog["listed_total"]}
        return [catalog, history, parts, weekly]

    def test_aligned_headers_allow_full_validation(self):
        self.assertEqual(audit.publication_alignment(*self.sources()), "aligned")

    def test_yesterdays_weekly_never_blocks_dart_source_only_publication(self):
        base = self.sources()
        base[3]["market_asof"] = "2026-10-29"
        base[3]["listed_total"] = base[0]["listed_total"] - 2
        self.assertEqual(audit.publication_alignment(*base), "deferred_weekly")
        # The previous combined result stays on its original 29th source day.
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "incompatible_saved_source_status"):
            audit.coverage(*base)

    def test_weekly_source_can_publish_before_later_annual_replay(self):
        base = self.sources()
        base[1]["asof"] = base[2]["asof"] = "2026-10-29"
        base[1]["listed_total"] = base[2]["listed_total"] = 2497
        self.assertEqual(audit.publication_alignment(*base), "deferred_dart")

    def test_mixed_fiscal_dates_future_and_false_certification_fail_closed(self):
        base = self.sources()
        bad = copy.deepcopy(base)
        bad[2]["asof"] = "2026-10-29"
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "invalid_source_alignment_scope"):
            audit.publication_alignment(*bad)
        bad = copy.deepcopy(base)
        bad[3]["market_asof"] = "2026-10-31"
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "invalid_source_alignment_scope"):
            audit.publication_alignment(*bad)
        bad = copy.deepcopy(base)
        bad[1]["historical_PIT_source_certified"] = True
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "unsafe_source_alignment_flags"):
            audit.publication_alignment(*bad)

    def test_publisher_defers_combined_audit_until_weekly_replay(self):
        root = Path(__file__).resolve().parent.parent
        bulk = (root/".github/workflows/v3-dart-bulk-four-year.yml").read_text()
        weekly = (root/".github/workflows/v3-weekly-rotation.yml").read_text()
        self.assertIn('ALIGNMENT="$(python v3_real_six_pillar_coverage_audit.py --mode alignment)"', bulk)
        self.assertIn("deferred_weekly", bulk)
        self.assertIn("fouryear_alignment_changed_after_rebase", bulk)
        self.assertIn('POST_ALIGNMENT="$(python v3_real_six_pillar_coverage_audit.py --mode alignment)"', weekly)
        self.assertIn("python v3_real_source_progress_ledger.py --mode verify", weekly)
        self.assertIn("'43 11 * * *'", weekly)
        self.assertIn("'29 * * * *'", bulk)


class RealCoverageAuditTests(unittest.TestCase):
    def setUp(self):
        catalog,bulk,parts,weekly=actual_inputs()
        day=catalog["date"]
        if any(d!=day for d in (
                bulk["asof"],parts["asof"],weekly["market_asof"])):
            self.skipTest("saved_research_sources_await_same_market_date")
    def test_actual_saved_sources_are_reconciled_with_no_fake_100point(self):
        sources=actual_inputs()
        out=audit.coverage(*sources)
        self.assertEqual(out["listed_total"],sources[0]["listed_total"])
        self.assertEqual(out["verified_four_year_income_reference_issuers"],
                         sources[1]["four_year_income_observed_count"])
        self.assertEqual(out["historical_dart_distinct_issuer_count"],
                         len(sources[1]["records"]))
        self.assertEqual(out["six_pillar_official_complete_100pt_issuer_count"],0)
        self.assertEqual(out["weight_contract"],{
            "ias":20,"technical":25,"financial":20,
            "valuation":20,"momentum":15})
        self.assertNotIn("industry",out["weight_contract"])
        self.assertEqual(sum(out["weight_contract"].values()),100)
        self.assertEqual(out["official_investor_IAS_source_issuer_count"],0)
        self.assertEqual(out["six_pillar_numeric_provisional_issuer_counts"]["ias"],0)
        self.assertEqual(out["six_pillar_numeric_provisional_issuer_counts"]["industry"],0)
        # Four-year growth and three-year operating margin are independent
        # genuine evidence. Do not mistake their union for 4Y completeness.
        finance=out["six_pillar_numeric_provisional_issuer_counts"]["financial"]
        growth=sources[2]["companies_with_computable_4y_income_scores"]
        margin=sources[2]["companies_with_computable_3y_margin_scores"]
        # Balance-sheet factors added later can make the observed union
        # larger than operating-margin coverage. Count only real rows with
        # nonzero AVAILABLE evidence; do not force it to the old margin count.
        self.assertEqual(finance,sum(
            r["financial_available_max"]>0 for r in sources[2]["rows"]))
        self.assertGreaterEqual(finance,margin)
        self.assertGreaterEqual(finance,growth)
        self.assertEqual(
            out["three_year_margin_provisional_score_issuers"],margin)
        self.assertEqual(
            out["verified_four_year_income_reference_issuers"],growth)
        self.assertLessEqual(
            out["four_numerical_areas_same_date_overlap_count"],finance)
        self.assertEqual(out["six_pillar_numeric_provisional_issuer_counts"]["technical"],
                         sum(rec.get("market_source_asof")==sources[0]["date"] and
                             isinstance(rec.get("research_score_points"),(int,float)) and
                             isinstance(rec.get("available_weight"),(int,float)) and
                             0<rec["available_weight"]<=25 and
                             0<=rec["research_score_points"]<=rec["available_weight"]
                             for rec in sources[3]["records"].values()
                             if rec.get("cycle")==sources[3]["cycle_start"]))
        self.assertFalse(out["buy_recommendations_authorized"])
        self.assertFalse(out["investment_grade_scores_confirmed"])
        overlap=out["four_numerical_areas_same_date_overlap_count"]
        self.assertLessEqual(overlap,
                             out["six_pillar_numeric_provisional_issuer_counts"]["financial"])
        self.assertLessEqual(len(out["four_numerical_areas_diagnostic_sample_codes"]),50)
        self.assertTrue(all(len(h)==64 for h in
                       out["source_sha256_canonical"].values()))
        self.assertEqual(out,audit.coverage(*sources))

    def test_actual_latest_annual_sales_to_market_cap_is_source_replayed(self):
        catalog,history,_,weekly=actual_inputs()
        book=json.loads((Path(__file__).resolve().parent.parent/"docs"/
                         "v3-dart-book-reference.json").read_text(encoding="utf8"))
        parts=research_parts(history,catalog,book)
        observed=audit.coverage(catalog,history,parts,weekly)
        self.assertGreater(parts["companies_with_computable_sales_to_cap_scores"],0)
        self.assertGreater(parts["companies_with_computable_liabilities_to_assets_scores"],0)
        self.assertGreater(parts["companies_with_computable_operating_roa_scores"],0)
        self.assertGreaterEqual(observed["six_pillar_numeric_provisional_issuer_counts"]
                                ["valuation"],parts["companies_with_computable_sales_to_cap_scores"])
        self.assertEqual(observed["six_pillar_official_complete_100pt_issuer_count"],0)
        target=next(r for r in parts["rows"] if
                    r["unverified_components"]["sales_to_market_cap"]["candidate_points"] is not None)
        broken=copy.deepcopy(parts)
        item=next(r for r in broken["rows"] if r["code"]==target["code"])
        item["unverified_components"]["sales_to_market_cap"]["observed_value"]+=0.1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "sales_to_cap_source_ratio_mismatch"):
            audit.coverage(catalog,history,broken,weekly)
        claimed=copy.deepcopy(parts)
        claimed["companies_with_computable_sales_to_cap_scores"]+=1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "research_component_evidence_count_mismatch"):
            audit.coverage(catalog,history,claimed,weekly)
        debt=next(r for r in parts["rows"] if r["unverified_components"]
                  ["liabilities_to_assets_pct"]["candidate_points"] is not None)
        altered=copy.deepcopy(parts)
        debt_row=next(r for r in altered["rows"] if r["code"]==debt["code"])
        debt_row["unverified_components"]["liabilities_to_assets_pct"][
            "observed_value"]+=0.2
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "balance_source_ratio_mismatch"):
            audit.coverage(catalog,history,altered,weekly)
        inflated=copy.deepcopy(parts)
        inflated["companies_with_computable_operating_roa_scores"]+=1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                    "research_component_evidence_count_mismatch"):
            audit.coverage(catalog,history,inflated,weekly)

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
        altered[2]["companies_with_computable_3y_margin_scores"]+=1
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "research_component_evidence_count_mismatch"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        altered[2]["rows"].append({
            **altered[2]["rows"][0],
            "financial_available_max":0,
            "financial_candidate_points":None,
            "valuation_available_max":0,
            "valuation_candidate_points":None,
            "combined_available_max":0,
            "combined_provisional_points":0,
            "unverified_components":{
                k:{**v,"max_points":0,"candidate_points":None}
                for k,v in altered[2]["rows"][0]["unverified_components"].items()
            },
        })
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "duplicate_research_issuer"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        altered[2]["rows"][0]["combined_available_max"]=100
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "invented_partial_coverage_or_total"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        altered[2]["rows"][0]["combined_provisional_points"]=100
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "invented_partial_coverage_or_total"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        altered[2]["rows"][0]["unverified_components"]["pbr"]["candidate_points"]=15
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "invented_partial_component_points"):
            audit.coverage(*altered)
        altered=copy.deepcopy(base)
        altered[1]["records"]["999999"]={"years":{}}
        with self.assertRaisesRegex(audit.CoverageAuditError,
                                     "unexpected_historical_issuer"):
            audit.coverage(*altered)

if __name__=="__main__":
    unittest.main()

class SameCycleWeeklyRolloverEvidenceTests(unittest.TestCase):
    def test_only_same_market_day_earns_weekly_technical_momentum(self):
        members={c:{} for c in ("000001","000002","000003")}
        cycle="2026-10-20"
        def record(code,asof):
            return {"code":code,"cycle":cycle,
                "market_source_asof":asof,"last_recalculated_kst":asof,
                "research_score_points":0.0,"available_weight":11,
                "momentum_research_points":3.0,"momentum_available_weight":8}
        data={"cycle_start":cycle,"run_kst":"2026-10-21",
            "processed_this_cycle":2,"unprocessed_this_cycle":1,
            "numeric_technical_candidates_this_cycle":2,
            "records":{
                "000001":record("000001","2026-10-20"),
                "000002":record("000002","2026-10-21"),
            }}
        tech,mom,old,current=audit.weekly_current_day_evidence(
            data,"2026-10-21",members)
        self.assertEqual(tech,{"000002"})
        self.assertEqual(mom,{"000002"})
        self.assertEqual(old,1)
        self.assertEqual(current,2)
        # A previous cycle's cached record may be stored, but must not
        # masquerade as this week's inspected evidence.
        data["records"]["000003"]={
            **record("000003","2026-10-10"),"cycle":"2026-10-13"}
        tech,mom,old,current=audit.weekly_current_day_evidence(
            data,"2026-10-21",members)
        self.assertEqual((tech,mom,old,current),({"000002"},{"000002"},1,2))

    def test_future_expired_forged_or_inflated_weekly_evidence_rejected(self):
        members={"000001":{}}
        clean={
          "cycle_start":"2026-10-20","run_kst":"2026-10-21",
          "processed_this_cycle":1,"unprocessed_this_cycle":0,
          "numeric_technical_candidates_this_cycle":1,
          "records":{"000001":{
              "code":"000001","cycle":"2026-10-20",
              "market_source_asof":"2026-10-20",
              "last_recalculated_kst":"2026-10-20",
              "research_score_points":5.0,"available_weight":11,
              "momentum_research_points":1.0,"momentum_available_weight":8}}
        }
        for mutation in (
            lambda x:x["records"]["000001"].update(market_source_asof="2026-10-22"),
            lambda x:x["records"]["000001"].update(market_source_asof="2026-09-10"),
            lambda x:x["records"]["000001"].update(last_recalculated_kst="2026-10-22"),
            lambda x:x.update(numeric_technical_candidates_this_cycle=2),
            lambda x:x.update(processed_this_cycle=0),
            lambda x:x["records"]["000001"].update(market_source_asof="not-a-date"),
        ):
            altered=copy.deepcopy(clean)
            mutation(altered)
            with self.assertRaises(audit.CoverageAuditError):
                audit.weekly_current_day_evidence(
                    altered,"2026-10-21",members)


class RealCoverageRolloverTests(unittest.TestCase):
    def test_cross_day_official_annual_evidence_rejected(self):
        catalog,bulk,parts,weekly=actual_inputs()
        altered=copy.deepcopy(bulk)
        altered["asof"]="2026-10-19" if catalog["date"]!="2026-10-19" else "2026-10-18"
        with self.assertRaises(audit.CoverageAuditError):
            audit.coverage(catalog,altered,parts,weekly)
