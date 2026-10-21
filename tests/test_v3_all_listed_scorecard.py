"""No-network all-current-listed output and missing-data invariants."""
import copy
import csv
import json
from io import StringIO
from pathlib import Path
import unittest

import v3_all_listed_scorecard as m
from v3_dart_cashflow_factor_preview import score_preview

DOCS=Path(__file__).resolve().parent.parent/"docs"


def read(name):
    return json.loads((DOCS/name).read_text(encoding="utf8"))


def sources():
    return [read(n) for n in (
        "data.json","v22-market-catalog.json","v3-dart-bulk-history.json",
        "v3-dart-bulk-provisional.json","v3-weekly-rotation.json",
        "v3-dart-cashflow-reference.json","v3-dart-fcf-research-preview.json")]


class AllListedScorecardTests(unittest.TestCase):
    def setUp(self):
        # Live integration assertions require the exact same closing date.
        # Market close may run ahead of bounded official DART backfills.
        snapshot,catalog,bulk,parts,weekly,_,_=sources()
        day=catalog["date"]
        if any(value!=day for value in (
                snapshot["date"],bulk["asof"],parts["asof"],
                weekly["market_asof"])):
            self.skipTest("source_day_rollover_waiting_for_official_dart_backfill")
    def test_every_catalog_issuer_even_without_usable_price(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        rows,report=m.generate(market,catalog,history,parts,weekly,cash,fcf)
        self.assertEqual(len(rows),catalog["listed_total"])
        self.assertEqual([r["code"] for r in rows],
                         sorted(r["code"] for r in catalog["rows"]))
        self.assertEqual(len({r["code"] for r in rows}),len(rows))
        self.assertEqual(report["full_six_pillar_certified_count"],0)
        self.assertNotIn("ias",m.WEIGHTS)
        self.assertEqual(m.WEIGHTS["volume"],20)
        self.assertEqual(report["provisional_numeric_issuer_counts"]["volume"],
                         sum(r["factors"]["volume"][1]>0 for r in rows))
        self.assertFalse(any(r["factors"]["industry"][1] for r in rows))
        self.assertTrue(all(r["available"]<=100 and
                            r["unavailable"]==100-r["available"] for r in rows))
        self.assertTrue(any(r["available"]>0 for r in rows))
        self.assertEqual(report["no_numeric_factor_issuer_count"]+
                         report["at_least_one_numeric_factor_count"],len(rows))
        self.assertTrue(report["volume20_replaces_unavailable_investor_IAS20"])
        self.assertEqual(report["legacy_foreign_institutional_IAS_verified_count"],0)
        self.assertEqual(report["volume20_observed_maximum_per_issuer"],16)
        self.assertTrue(report["technical_one_day_volume_not_double_counted"])
        self.assertEqual(report["provisional_numeric_issuer_counts"]["industry"],0)
        text=m.to_csv(rows,report)
        self.assertTrue(text.startswith("\ufeff"))
        parsed=list(csv.DictReader(StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(len(parsed),len(rows))
        self.assertIn("거래량·OBV_잠정점수",parsed[0])
        self.assertNotIn("수급_잠정점수",parsed[0])
        self.assertEqual(parsed[0]["종목코드"],rows[0]["code"])
        self.assertTrue(all(r["산업_잠정점수"]=="" and
                            r["정식투자점수"]=="" for r in parsed))
        self.assertTrue(all(r["인증상태"]=="미검증·연구용" for r in parsed))
        never=sum(1 for row in rows if row["available"]==0)
        self.assertEqual(never,report["no_numeric_factor_issuer_count"])
        self.assertTrue(all(not parsed[i]["V3잠정합계"] for i,r in enumerate(rows)
                            if r["available"]==0))
        self.assertTrue(all(parsed[i]["V3잠정합계"]!="" for i,r in enumerate(rows)
                            if r["available"]>0))
        self.assertEqual(report["score_state"],
            "provisional_observed_only_not_investment_grade")

    def test_user_approved_six_area_100_with_industry5433(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        rows,summary=m.generate(market,catalog,history,parts,weekly,cash,fcf)
        self.assertEqual(sum(m.WEIGHTS.values()),100)
        self.assertEqual(summary["industry_approved_subweights"],{
            "early_rotation":5,"profit_transition":4,
            "improvement_diffusion":3,"overheat_crowding_quality":3})
        self.assertEqual(sum(summary["industry_approved_subweights"].values()),15)
        self.assertNotIn("five_area_optional_comparison",summary)
        self.assertFalse(any(row["factors"]["industry"][1] for row in rows))
        self.assertEqual(len(rows),2497)
        for row in rows:
            self.assertEqual(row["available"]+row["unavailable"],100)
        csv_data=list(csv.DictReader(StringIO(m.to_csv(
            rows,summary).lstrip("\ufeff"))))
        self.assertEqual(len(csv_data),len(rows))
        self.assertNotIn("5영역_산업제외_잠정점수_85점중",csv_data[0])
        for got,row in zip(csv_data,rows):
            self.assertEqual(got["산업_분류_참고용"],
                             row["industry_label_unverified"] or "")
            self.assertEqual(got["산업_잠정점수"],"")
            self.assertEqual(got["정식투자점수"],"")

    def test_official_current_industry_codes_display_without_industry_points(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        industry=read("v3-dart-industry-classification.json")
        rows,report=m.generate(market,catalog,history,parts,weekly,cash,fcf,
                               industry)
        by_code={r["code"]:r for r in rows}
        self.assertEqual(report["industry_current_company_source_status"],
                         "verified_current_company_classification_only")
        self.assertEqual(report["industry_code_source_observed"],
                         industry["observed_industry_codes"])
        self.assertGreater(report["industry_code_source_observed"],0)
        self.assertEqual(report["industry_code_source_sector15_earned_count"],0)
        self.assertEqual(report["provisional_numeric_issuer_counts"]["industry"],0)
        for code,value in industry["records"].items():
            self.assertEqual(by_code[code]["dart_current_industry_code"],
                             value["industry_code"])
            self.assertEqual(by_code[code]["factors"]["industry"],(None,0))
        csv_rows=list(csv.DictReader(StringIO(m.to_csv(
            rows,report).lstrip("\ufeff"))))
        self.assertIn("DART_현재업종코드_산업점수아님",csv_rows[0])
        first=industry["records"].keys().__iter__().__next__()
        self.assertEqual(next(r for r in csv_rows if r["종목코드"]==first)
                         ["DART_현재업종코드_산업점수아님"],
                         industry["records"][first]["industry_code"])

        altered=copy.deepcopy(industry)
        altered["records"][first]["industry_code"]="999999"
        discarded,blocked=m.generate(
            market,catalog,history,parts,weekly,cash,fcf,altered)
        self.assertEqual(blocked["industry_current_company_source_status"],
                         "source_rejected")
        self.assertEqual(blocked["industry_code_source_observed"],0)
        self.assertIsNone(blocked["source_sha256"]["industry_company_profile"])
        self.assertFalse(any(r["dart_current_industry_code"] for r in discarded))
        self.assertEqual(blocked["provisional_numeric_issuer_counts"],
                         report["provisional_numeric_issuer_counts"])

    def test_source_depth_matches_same_saved_verified_annual_and_fcf_inputs(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        industry=read("v3-dart-industry-classification.json")
        rows,report=m.generate(market,catalog,history,parts,weekly,cash,fcf,
                               industry)
        from v3_real_six_pillar_coverage_audit import coverage
        actual=coverage(catalog,history,parts,weekly)
        depth=report["observed_source_depth"]
        self.assertEqual(depth["four_year_dart_attempted_issuers"],
                         actual["historical_dart_distinct_issuer_count"])
        self.assertEqual(depth["four_year_income_observed_issuers"],
                         actual["verified_four_year_income_reference_issuers"])
        self.assertEqual(depth["three_year_margin_observed_issuers"],
                         actual["three_year_actual_margin_source_issuers"])
        self.assertEqual(depth["three_year_receipt_matched_fcf_issuers"],
            cash["metrics"]["three_year_fcf_receipt_matched_issuers"]
            if report["fcf_source_added"] else None)
        self.assertEqual(depth["current_company_industry_code_issuers"],
                         report["industry_code_source_observed"])
        self.assertEqual(report["source_sha256"]["dart_major"],
                         m._stable_digest(history))
        self.assertEqual(report["source_sha256"]["dart_partial"],
                         m._stable_digest(parts))
        self.assertEqual(report["source_sha256"]["weekly"],
                         m._stable_digest(weekly))
        self.assertEqual(len(rows),2497)
        self.assertEqual(report["full_six_pillar_certified_count"],0)

        corrupted=copy.deepcopy(cash)
        corrupted["metrics"]["three_year_fcf_receipt_matched_issuers"]+=1
        _,blocked=m.generate(market,catalog,history,parts,weekly,
                             corrupted,fcf,industry)
        self.assertFalse(blocked["fcf_source_added"])
        self.assertIsNone(blocked["observed_source_depth"]
                          ["three_year_receipt_matched_fcf_issuers"])
        self.assertEqual(blocked["full_six_pillar_certified_count"],0)

    def test_fcf_only_when_exact_recomputed_source_matches(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        _,base=m.generate(market,catalog,history,parts,weekly)
        rows,real=m.generate(market,catalog,history,parts,weekly,cash,fcf)
        self.assertTrue(real["fcf_source_added"] in (True,False))
        tampered=copy.deepcopy(fcf)
        if tampered["rows"]:
            tampered["rows"][0]["scoring_certified"]=True
            other,report=m.generate(
                market,catalog,history,parts,weekly,cash,tampered)
            self.assertFalse(report["fcf_source_added"])
            self.assertEqual(report["provisional_numeric_issuer_counts"],
                             base["provisional_numeric_issuer_counts"])
            self.assertIn("volume",other[0]["factors"])
            self.assertLessEqual(other[0]["factors"]["volume"][1],16)
        future=copy.deepcopy(fcf)
        future["asof"]="2026-10-22"
        _,blocked=m.generate(market,catalog,history,parts,weekly,cash,future)
        self.assertFalse(blocked["fcf_source_added"])
        self.assertIsNone(blocked["source_sha256"]["fcf"])
        # No certification is produced regardless of whether independently
        # stored annual receipts are still aligned with the moving major book.
        self.assertEqual(real["full_six_pillar_certified_count"],0)

    def test_older_fcf_preview_recalculated_from_saved_real_receipts(self):
        market,catalog,history,parts,weekly,cash,_=sources()
        if cash["asof"]!=market["date"]:
            self.skipTest("older_cashflow_receipts_cannot_be_attached_to_new_market_date")
        current=score_preview(market,catalog,history,cash,
                              minimum_peers=30)
        stale=copy.deepcopy(current)
        stale["major_history_sha256"]="0"*64
        rows,report=m.generate(
            market,catalog,history,parts,weekly,cash,stale)
        self.assertTrue(report["fcf_source_added"])
        self.assertEqual(report["fcf_source_state"],
                         "recomputed_from_saved_verified_cashflow")
        self.assertEqual(report["source_sha256"]["fcf"],
                         m._stable_digest(current))
        self.assertEqual(len(rows),catalog["listed_total"])
        self.assertEqual(report["full_six_pillar_certified_count"],0)
        unsafe=copy.deepcopy(stale)
        unsafe["rows"][0]["scoring_certified"]=True
        _,blocked=m.generate(market,catalog,history,parts,weekly,cash,unsafe)
        self.assertFalse(blocked["fcf_source_added"])

    def test_missing_and_invalid_factors_do_not_become_zero(self):
        self.assertEqual(m._contribution(None,0,15),(None,0))
        self.assertEqual(m._contribution(0,5,15),(0.0,5))
        for wrong in ((7,0,15),(16,15,15),(None,4,15),
                      (10,16,15),(-1,4,15)):
            with self.assertRaises(m.FullMarketScorecardError):
                m._contribution(*wrong)
        self.assertEqual(m._momentum({}, "2026-10-20","KOSPI"),(None,0))
        market,catalog,history,parts,weekly,cash,fcf=sources()
        broken=copy.deepcopy(parts)
        if broken["rows"]:
            broken["rows"][0]["combined_available_max"]=100
            with self.assertRaises(Exception):
                m.generate(market,catalog,history,broken,weekly)
        incomplete=copy.deepcopy(weekly)
        incomplete["numeric_technical_candidates_this_cycle"]+=1
        with self.assertRaises(Exception):
            m.generate(market,catalog,history,parts,incomplete)


if __name__=="__main__":
    unittest.main()

class ScorecardDateGateTests(unittest.TestCase):
    def test_market_rollover_never_publishes_false_new_date_points(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        original=copy.deepcopy(history)
        current=catalog["date"]
        # Simulate legitimate market refresh before annual filing refresh.
        shifted=copy.deepcopy(history)
        shifted["asof"]="2026-10-19" if current!="2026-10-19" else "2026-10-18"
        from v3_real_six_pillar_coverage_audit import CoverageAuditError
        with self.assertRaises(CoverageAuditError):
            m.generate(market,catalog,shifted,parts,weekly,cash,fcf)
        self.assertEqual(original,history)
