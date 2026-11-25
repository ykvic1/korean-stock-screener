from copy import deepcopy
import unittest

from v3_v30b_operational_release import evaluate, CUTOFF, COMMIT, BANDS, WEIGHTS, AUDIT_CHECKS


def fixture():
    scope = {"development_cutoff_kst": CUTOFF,
             "advance_development_cutoff_on_new_market_data": False,
             "post_cutoff_ingestion": "scheduled_operations",
             "wave_validation_required": False, "forward_performance_required_for_release": False,
             "operational_development_acceptance": {"deployed_commit": COMMIT,
                 "baseline_asof": CUTOFF, "listed_total": 10, "ranked_count": 9},
             "remaining_fixed_snapshot_verification": ["historical financial research"]}
    dist = dict(zip(BANDS, (2, 1, 2, 1, 1, 1, 2)))
    audit = {"asof": CUTOFF, "publication_coherent": True,
             "checks": dict.fromkeys(AUDIT_CHECKS, True), "unexplained_unpriced_count": 0,
             "pages": {"status": "coherent"}, "available_weight_distribution": dist,
             "listed_total": 10, "investment_score_certified": False,
             "performance_validated": False, "provider_calls": 0}
    comparison = {"asof": CUTOFF, "operational_coverage": {
        "listed_total": 10, "ranked_count": 9, "available_100_count": 2,
        "available_95_count": 3, "available_90_count": 5, "budgets": WEIGHTS.copy(),
        "missing_is_not_zero": True, "investment_score_certified": False,
        "performance_validated": False, "provider_calls": 0}}
    closeout = {"schema": "v3-initial-data-collection-closeout-1", "asof": "2026-11-16",
        "initial_data_collection_complete": True, "missing_data_excluded_from_completion": True,
        "all_listed_100_weight_required": False, "missing_values_filled_or_weights_normalized": False,
        "daily_refresh_continues": True, "investment_score_certified": False,
        "performance_validated": False, "checks": dict.fromkeys(
            ("publication_coherent", "available_fcf_sources_attempted",
             "historical_receipt_collection_complete", "fixed_krx_window_extracted"), True)}
    gap = {"asof": CUTOFF, "listed_issuer_count": 10,
           "source_eligible_unfetched_company_years": 0,
           "missing_ifrs_account_not_interpreted_as_zero": True}
    receipts = {"filing_months_checkpointed": 120, "filing_months_total": 120,
                "filing_months_remaining": 0}
    krx = {"five_session_cohorts_completed": 490, "five_session_cohorts_target": 490,
           "cross_cohort_bridges_completed": 489, "cross_cohort_bridges_required_for_completed_source": 489,
           "source_history_coverage_ratio": 1}
    return [scope, COMMIT, audit, comparison, closeout, gap, receipts, krx]


class FrozenReleaseTests(unittest.TestCase):
    def test_missing_observations_and_uncertified_history_do_not_block_release(self):
        args = fixture()
        result = evaluate(*args)
        self.assertTrue(result["development_complete"])
        self.assertTrue(result["operational_release_ready"])
        self.assertFalse(result["investment_score_certified"])
        self.assertFalse(result["daily_data_changes_reopen_development"])
        self.assertEqual(result["available_weight_distribution"]["100"], 2)

    def test_new_operational_day_or_historical_followup_cannot_reopen_release(self):
        args = fixture()
        args[0]["latest_operational_day"] = "2027-01-02"
        args[0]["remaining_fixed_snapshot_verification"] = ["another research gap"]
        self.assertTrue(evaluate(*args)["development_complete"])

    def test_bad_frozen_data_or_certification_claims_fail_closed(self):
        for mutate in (
            lambda a: a.__setitem__(1, "other_commit"),
            lambda a: a[0].update(development_cutoff_kst="2026-11-24"),
            lambda a: a[2]["checks"].update(every_operational_csv_cell=False),
            lambda a: a[3].update(asof="2026-11-24"),
            lambda a: a[3]["operational_coverage"].update(available_100_count=10),
            lambda a: a[4].update(missing_values_filled_or_weights_normalized=True),
            lambda a: a[5].update(source_eligible_unfetched_company_years=1),
            lambda a: a[6].update(filing_months_remaining=1),
            lambda a: a[7].update(cross_cohort_bridges_completed=488),
            lambda a: a[2].update(investment_score_certified=True),
        ):
            args = fixture()
            mutate(args)
            r = evaluate(*args)
            self.assertFalse(r["operational_release_ready"])
            self.assertTrue(r["blockers"])
            self.assertFalse(r["investment_score_certified"])


if __name__ == "__main__":
    unittest.main()
