"""Offline contracts for manual 90-first source dispatch (no provider calls)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v3_manual_90first_source_backlog import source_queue_counts, should_dispatch, cashflow_queue_counts


FILES = (
    "v3-dart-bulk-history.json",
    "v3-dart-revenue-2022-supplement.json",
    "v3-dart-book-reference.json",
    "v3-near-100-evidence-priority.json",
    "v3-dart-owner-profit-supplement.json",
    "v3-operational-research-comparison.json",
    "v3-dart-reported-eps.json",
)


class ManualSourceBacklogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {"date": "2026-11-12", "listed_total": 2591}
        self.operational = {"asof": "2026-11-12", "listed_total": 2591}

    def test_verifiably_exhausted_receipt_queues_skip_redundant_workers(self):
        with tempfile.TemporaryDirectory() as d:
            for name in FILES:
                (Path(d) / name).write_text(json.dumps({"source": name}))
            with patch("v3_manual_90first_source_backlog.revenue_plan",
                       return_value=({"A": 1}, {"A": 1}, [])) as annual, patch(
                    "v3_manual_90first_source_backlog.profit_completion",
                    return_value=({}, {}, set(), {
                        "status": "all_listed_profit_source_queue",
                        "pending_all_listed_issuers": 0})) as profit:
                counts = source_queue_counts(d, self.catalog, self.operational)
            self.assertEqual(counts, {"annual": 0, "profit": 0})
            annual.assert_called_once()
            profit.assert_called_once()
            self.assertFalse(should_dispatch(94, counts["annual"]))
            self.assertFalse(should_dispatch(16, counts["profit"]))

    def test_unqueried_records_remain_eligible_even_if_point_ceiling_is_not_source(self):
        with tempfile.TemporaryDirectory() as d:
            for name in FILES:
                (Path(d) / name).write_text("{}")
            with patch("v3_manual_90first_source_backlog.revenue_plan",
                       return_value=({}, {}, ["000001"])), patch(
                    "v3_manual_90first_source_backlog.profit_completion",
                    return_value=({}, {}, set(), {
                        "status": "all_listed_profit_source_queue",
                        "pending_all_listed_issuers": 2})):
                counts = source_queue_counts(d, self.catalog, self.operational)
            self.assertEqual(counts, {"annual": 1, "profit": 2})
            self.assertTrue(should_dispatch(94, counts["annual"]))
            self.assertTrue(should_dispatch(16, counts["profit"]))

    def test_missing_invalid_or_stale_evidence_never_masquerades_as_zero_work(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(source_queue_counts(d, self.catalog, self.operational),
                             {"annual": None, "profit": None})
            self.assertTrue(should_dispatch(94, None))
            self.assertTrue(should_dispatch(16, None))
            self.assertFalse(should_dispatch(0, None))
            stale = dict(self.operational, asof="2026-11-11")
            self.assertEqual(source_queue_counts(d, self.catalog, stale),
                             {"annual": None, "profit": None})
            with self.assertRaises(ValueError):
                should_dispatch(3, -1)
            with self.assertRaises(ValueError):
                should_dispatch(-1, None)

    def test_invalid_source_plan_does_not_stop_other_source_families(self):
        with tempfile.TemporaryDirectory() as d:
            for name in FILES:
                (Path(d) / name).write_text("{}")
            with patch("v3_manual_90first_source_backlog.revenue_plan",
                       side_effect=ValueError("misaligned_receipt")), patch(
                    "v3_manual_90first_source_backlog.profit_completion",
                    return_value=({}, {}, set(), {
                        "status": "waiting_current_operational_snapshot",
                        "pending_all_listed_issuers": 0})):
                counts = source_queue_counts(d, self.catalog, self.operational)
            self.assertEqual(counts, {"annual": None, "profit": None})


class CashflowBacklogTests(unittest.TestCase):
    def check_queue(self, *, api_review=True, original_review=False, available=88, stale=False):
        catalog={"date":"2026-11-13","listed_total":1}
        report={"asof":"2026-11-12" if stale else catalog['date'],"listed_total":1,
                "rows":{"000001":{"available":available,"parts":{
                    "financial":{"cashflow_fcf_persistence":[0,0]},
                    "valuation":{"fcf_yield":[0,2.5]}}}}}
        year={"free_cashflow_krw":None,"operating_cashflow_krw":100,
              "major_annual_receipt_matched":True}
        if api_review:year['reported_total_review']={"reason":"no_reported_total"}
        if original_review:year['original_xbrl_review']={"reason":"no_verified_scope_anchor"}
        state={"records":{"000001":{"years":{"2024":year}}}}
        with tempfile.TemporaryDirectory() as d:
            for name in ('data.json','v3-dart-bulk-history.json',
                         'v3-dart-cashflow-reference.json','v3-operational-research-comparison.json'):
                (Path(d)/name).write_text('{}')
            with patch('v3_dart_cashflow_enrichment.retained_current_market_cashflow',return_value=state), \
                 patch('v3_dart_cashflow_enrichment.verify_state'), \
                 patch('v3_dart_xbrl_capex_pilot.operational_gap_priority',return_value={'000001'}):
                return cashflow_queue_counts(d,catalog,report)

    def test_exhausted_api_does_not_hide_original_file_work(self):
        self.assertEqual(self.check_queue(),{'reported_capex':0,'original_xbrl':1})
        self.assertEqual(self.check_queue(api_review=False),{'reported_capex':1,'original_xbrl':1})

    def test_reviewed_originals_are_not_refetched(self):
        self.assertEqual(self.check_queue(original_review=True),{'reported_capex':0,'original_xbrl':0})

    def test_outside_90_priority_is_not_a_new_broad_original_retry(self):
        self.assertEqual(self.check_queue(available=94),{'reported_capex':0,'original_xbrl':0})
        self.assertEqual(self.check_queue(available=80),{'reported_capex':0,'original_xbrl':0})

    def test_stale_missing_or_invalid_sources_are_unknown_not_zero(self):
        self.assertEqual(self.check_queue(stale=True),{'reported_capex':None,'original_xbrl':None})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cashflow_queue_counts(d,{'date':'2026-11-13','listed_total':1},
                {'asof':'2026-11-13','listed_total':1}),{'reported_capex':None,'original_xbrl':None})

    def test_workflow_dispatches_each_source_from_its_own_queue(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-manual-90first-source-burst.yml').read_text()
        self.assertIn('cash_pending["reported_capex"]',workflow)
        self.assertIn("if: steps.gaps.outputs.original_xbrl == 'true'",workflow)
        self.assertIn('v3-xbrl-cashflow-recovery.yml/dispatches',workflow)
        self.assertIn("'inputs[recheck_equivalent_contexts]=false'",workflow)


if __name__ == "__main__":
    unittest.main()
