"""Research-only 120-week evidence with rebalanced pattern-first technical 15; no inflation."""
import unittest

from v3_research_factor_engine import FIELD_SPECS, research_factor_batch
from v3_score_contract import PILLAR_POINTS

ASOF="2026-10-16"
LONG_WEEK="technical_weekly_ma120_reclaim_strength"


def cohort(*, n=40, market="KOSPI", missing=()):
    rows=[]
    for i in range(n):
        features={}
        for key, (_, _, bounds) in FIELD_SPECS.items():
            if bounds == (0, 1):
                x=(i+1)/(n+1)
            elif key == "valuation_pbr":
                x=0.8+i/n
            elif key == "technical_volume_breakout_ratio":
                x=0.5+i/n
            else:
                x=5.0+i
            features[key] = None if key in missing else x
        code_n=i+(100000 if market=="KOSDAQ" else 0)
        issuer_n=i+(10000000 if market=="KOSDAQ" else 0)
        rows.append({
            "code":f"{code_n:06d}",
            "issuer_id":f"{issuer_n:08d}",
            "market":market, "asof":ASOF, "features":features})
    return rows


class OptionalTechnicalFactorCoverageTests(unittest.TestCase):
    def test_missing_120week_gives_12_of_15_research_only_not_full_pillar(self):
        result=research_factor_batch(
            cohort(missing=(LONG_WEEK,)),asof=ASOF,min_peers=30)
        for row in result["rows"]:
            t=row["technical_subfactor_partial_research_only"]
            self.assertEqual(t["observed_maximum_points"],12)
            self.assertEqual(t["unavailable_maximum_points"],3)
            self.assertIsNone(t["individual_research_points"][LONG_WEEK])
            self.assertIn(LONG_WEEK,t["missing_or_insufficient_peer_metrics"])
            self.assertLessEqual(t["research_points_unscaled"],12)
            self.assertTrue(t["missing_120week_is_not_zero_or_reweighted"])
            self.assertFalse(t["equal_to_complete_technical_pillar"])
            self.assertIsNone(t["confirmed_technical_score"])
            self.assertIsNone(row["pillar_research_candidates"]["technical"])
            self.assertIsNone(row["candidate_full_total_research_only"])
            self.assertEqual(row["candidate_coverage_pct"],85.0)
            self.assertIsNone(row["confirmed_score"])
            self.assertIsNone(row["confirmed_rank"])

    def test_120week_with_peer_evidence_matches_original_complete_component(self):
        result=research_factor_batch(cohort(),asof=ASOF,min_peers=30)
        for row in result["rows"]:
            t=row["technical_subfactor_partial_research_only"]
            self.assertEqual(t["observed_maximum_points"],15)
            self.assertEqual(t["unavailable_maximum_points"],0)
            self.assertTrue(t["equal_to_complete_technical_pillar"])
            self.assertAlmostEqual(
                t["research_points_unscaled"],
                row["pillar_research_candidates"]["technical"],places=5)
            self.assertIsNone(t["confirmed_technical_score"])
            self.assertIsNone(row["confirmed_score"])

    def test_only_one_technical_metric_is_observed_not_stretched_to_full(self):
        missing=set(k for k in FIELD_SPECS if k.startswith("technical_"))
        missing.remove("technical_pattern_confirmation_strength")
        result=research_factor_batch(
            cohort(missing=missing),asof=ASOF,min_peers=30)
        for row in result["rows"]:
            t=row["technical_subfactor_partial_research_only"]
            self.assertEqual(t["observed_maximum_points"],7)
            self.assertEqual(t["unavailable_maximum_points"],8)
            self.assertLessEqual(t["research_points_unscaled"],7)
            self.assertIsNone(row["pillar_research_candidates"]["technical"])
            self.assertFalse(row["scoring_authorized_for_investment"])

    def test_no_technical_metrics_no_assumed_zero(self):
        missing=set(k for k in FIELD_SPECS if k.startswith("technical_"))
        r=research_factor_batch(cohort(missing=missing),asof=ASOF,min_peers=30)
        for row in r["rows"]:
            t=row["technical_subfactor_partial_research_only"]
            self.assertEqual(t["observed_maximum_points"],0)
            self.assertEqual(t["unavailable_maximum_points"],15)
            self.assertIsNone(t["research_points_unscaled"])
            self.assertEqual(len(t["observed_metrics"]),0)
            self.assertIsNone(row["confirmed_score"])

    def test_market_specific_peer_minimum_never_borrowed_cross_market(self):
        mix=cohort(n=10,market="KOSPI")+cohort(n=40,market="KOSDAQ")
        result=research_factor_batch(mix,asof=ASOF,min_peers=30)
        for row in result["rows"]:
            t=row["technical_subfactor_partial_research_only"]
            if row["market"]=="KOSPI":
                self.assertEqual(t["observed_maximum_points"],0)
                self.assertIsNone(t["research_points_unscaled"])
            else:
                self.assertEqual(t["observed_maximum_points"],PILLAR_POINTS["technical"])
                self.assertIsNotNone(t["research_points_unscaled"])


if __name__=="__main__":
    unittest.main()
