"""Frozen score to gated historical 5-horizon evaluation, synthetic only."""
import copy
import unittest
from test_v3_walkforward_quality_gate import candidate
from v3_frozen_score_backtest import scored_walkforward, ScoredBacktestError
from v3_walkforward_research import HORIZONS

def with_score(c=None):
    p=copy.deepcopy(c if c is not None else candidate())
    case=p["case"]
    p["frozen_score_snapshot"]={
        "code":case["code"],"issuer_id":case["issuer_id"],"market":case["market"],
        "asof":case["signal_date"],
        "frozen_at_utc":case["signal_date"]+"T07:00:00Z",
        "snapshot_sha256":case["evidence"]["signal_snapshot_sha256"],
        "candidate_full_total_research_only":83.5,
        "confirmed_score":None,"confirmed_buy":False,
    }
    return p

def with_five_factor_score(c=None, *, partial=False):
    p=with_score(c)
    score=p["frozen_score_snapshot"]
    del score["candidate_full_total_research_only"]
    score["scorecard_schema"]="v3-all-current-listed-observed-five-factor-scorecard-0.3"
    score["industry_excluded_from_score_budget"]=True
    score["missing_data_never_zero_filled"]=True
    score["factors"]={
        "volume":[18,20],"technical":[20,25],"financial":[15,20],
        "valuation":[15,20],"momentum":[10,15],
    }
    if partial:
        score["factors"]["valuation"]=[None,0]
        score["factors"]["volume"]=[10,16]
    score["available"]=sum(row[1] for row in score["factors"].values())
    score["earned"]=sum(row[0] or 0 for row in score["factors"].values())
    return p

def evaluate(v):
    return scored_walkforward(v,split_date="2026-01-01")

class FrozenScoreWalkforwardTests(unittest.TestCase):
    def test_five_horizons_linked_to_quality_without_issuer_recommendation(self):
        r=evaluate([with_score()])
        self.assertEqual(r["input_case_count"],1)
        self.assertEqual(set(HORIZONS),set(r["research_quality_gated_walkforward"]["periods"]))
        for name in HORIZONS:
            cell=r["frozen_score_vs_outcomes_research_only"]["KOSPI"][name]
            self.assertEqual(cell["cohorts"]["train"]["eligible_score_label_pairs"],1)
            self.assertFalse(cell["cohorts"]["train"]["exploratory_sample_floor_met"])
            self.assertIsNone(cell["cohorts"]["train"][
                "score_to_realized_net_return_spearman_research_only"])
        self.assertFalse(r["model_validated_out_of_sample"])
        self.assertIsNone(r["forecast"])
        self.assertFalse(r["confirmed_buy"])

    def test_hindsight_score_date_or_timestamp_rejected(self):
        changes=[
            lambda p:p["frozen_score_snapshot"].update(asof="2026-01-01"),
            lambda p:p["frozen_score_snapshot"].update(
                frozen_at_utc=p["case"]["signal_date"]+"T07:15:00Z"),
            lambda p:p["frozen_score_snapshot"].update(
                frozen_at_utc=p["case"]["signal_date"]+"T05:00:00Z"),
            lambda p:p["frozen_score_snapshot"].update(
                snapshot_sha256="b"*64),
            lambda p:p["frozen_score_snapshot"].update(
                issuer_id="99999999"),
            lambda p:p["frozen_score_snapshot"].update(
                confirmed_buy=True),
            lambda p:p["frozen_score_snapshot"].update(
                candidate_full_total_research_only=101),
        ]
        for fn in changes:
            p=with_score()
            fn(p)
            with self.assertRaises(ScoredBacktestError):
                evaluate([p])

    def test_event_at_signal_censors_all_cohorts(self):
        p=with_score()
        p["price_record"]["events"]=[{
            "kind":"split","effective_date":p["case"]["signal_date"],
            "source_sha256":"f"*64}]
        r=evaluate([p])
        for h in HORIZONS:
            c=r["frozen_score_vs_outcomes_research_only"]["KOSPI"][h]
            self.assertEqual(c["cohorts"]["train"]["eligible_score_label_pairs"],0)
            self.assertEqual(c["exclusions"]["quality_censored"],1)

    def test_mismatched_price_cannot_backtest_frozen_score(self):
        p=with_score()
        p["case"]["bars"][0]["close"]=99
        with self.assertRaisesRegex(ScoredBacktestError,"quality_gate"):
            evaluate([p])

    def test_future_filing_not_used_to_legitimize_prior_score(self):
        p=with_score()
        p["pit_bundle"]["flow_ledger"]["observed_at_utc"]="2026-10-19T00:00:00Z"
        r=evaluate([p])
        self.assertEqual(
            r["frozen_score_vs_outcomes_research_only"]["KOSPI"]["1m"]
             ["exclusions"]["quality_censored"],1)

    def test_duplicate_issuer_signal_and_missing_freeze_are_blocked(self):
        p=with_score()
        with self.assertRaisesRegex(ScoredBacktestError,"duplicate"):
            evaluate([p,copy.deepcopy(p)])
        with self.assertRaises(ScoredBacktestError):
            evaluate([{"case":p["case"]}])

    def test_short_history_censored_by_each_horizon(self):
        p=with_score(candidate(history=40))
        r=evaluate([p])
        self.assertEqual(r["frozen_score_vs_outcomes_research_only"][
            "KOSPI"]["1m"]["cohorts"]["train"]["eligible_score_label_pairs"],1)
        self.assertEqual(r["frozen_score_vs_outcomes_research_only"][
            "KOSPI"]["12m"]["exclusions"]["short_history"],1)


    def test_five_factor_full_budget_uses_observed_points_not_legacy_score(self):
        p=with_five_factor_score()
        r=evaluate([p])
        self.assertEqual(r["frozen_score_contract"],"five_factor_observed_partial")
        self.assertTrue(r["partial_scores_never_normalized_or_zero_filled"])
        self.assertEqual(r["frozen_score_vs_outcomes_research_only"]["KOSPI"][
            "1m"]["cohorts"]["train"]["eligible_score_label_pairs"],1)
        self.assertEqual(r["frozen_score_vs_outcomes_research_only"]["KOSPI"][
            "1m"]["exclusions"]["partial_score_coverage_noncomparable"],0)
        self.assertIsNone(r["forecast"])
        self.assertFalse(r["v3_release_ready"])

    def test_partial_five_factor_score_is_not_misread_as_full_or_zero(self):
        p=with_five_factor_score(partial=True)
        score=p["frozen_score_snapshot"]
        self.assertEqual(score["available"],76)
        self.assertEqual(score["earned"],55)
        r=evaluate([p])
        for horizon in HORIZONS:
            cell=r["frozen_score_vs_outcomes_research_only"]["KOSPI"][horizon]
            self.assertEqual(cell["cohorts"]["train"]["eligible_score_label_pairs"],0)
            self.assertEqual(cell["exclusions"][
                "partial_score_coverage_noncomparable"],1)
        # The underlying event/PIT engine still audits this signal and its labels.
        self.assertEqual(r["research_quality_gated_walkforward"]["periods"][
            "1m"]["internally_eligible_cases_submitted"],1)

    def test_five_factor_hindsight_contract_and_missingness_fails_closed(self):
        changes={
            "claimed_available_inflated":lambda s:s.update(available=100),
            "industry_score_included":lambda s:s["factors"].update(industry=[3,5]),
            "missing_as_zero":lambda s:s["factors"]["valuation"].__setitem__(0,0),
            "coverage_mismatch":lambda s:s["factors"]["volume"].__setitem__(1,20),
            "earned_mismatch":lambda s:s.update(earned=99),
            "wrong_source_schema":lambda s:s.update(scorecard_schema="different"),
            "legacy_total_mixed":lambda s:s.update(
                candidate_full_total_research_only=87),
            "industry_claim_false":lambda s:s.update(
                industry_excluded_from_score_budget=False),
        }
        for label,mutate in changes.items():
            with self.subTest(label=label):
                p=with_five_factor_score(partial=True)
                mutate(p["frozen_score_snapshot"])
                with self.assertRaises(ScoredBacktestError):
                    evaluate([p])

    def test_five_and_legacy_score_cohorts_cannot_be_pooled(self):
        p=with_five_factor_score()
        older=with_score(candidate(signal_idx=25))
        with self.assertRaisesRegex(ScoredBacktestError,
                                    "mixed_frozen_score_contracts"):
            evaluate([p,older])


if __name__=="__main__":
    unittest.main()
