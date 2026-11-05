"""Annual revisions quarantine one period, not unrelated published FCF."""
import copy
import unittest

from test_v3_dart_cashflow_factor_preview import five_complete
from v3_dart_cashflow_factor_preview import score_preview
from v3_current_observed_scorecard import cashflow_source, LivePartialScorecardError


class CashflowPublicationReconciliationTests(unittest.TestCase):
    def test_new_receipt_keeps_other_issuers_without_relabeling_old_values(self):
        market,catalog,major,cash=five_complete()
        before=copy.deepcopy(cash)
        preview=score_preview(market,catalog,major,cash,minimum_peers=30)
        code=next(iter(cash['records']))
        revised=copy.deepcopy(major)
        revised['records'][code]['years']['2025']['CFS']['receipt_id']='20260325000002'
        rows,current=cashflow_source(market,catalog,revised,cash,preview)
        self.assertNotIn(code,rows)
        self.assertEqual(set(rows),set(cash['records'])-{code})
        self.assertEqual(current['real_fcf_assets_three_year_raw_issuer_count'],4)
        self.assertEqual(current['real_latest_fcf_yield_raw_issuer_count'],4)
        self.assertEqual(cash,before)
        self.assertFalse(current['hist_PIT_verified'])

    def test_same_source_tampered_preview_still_rejected(self):
        market,catalog,major,cash=five_complete()
        preview=score_preview(market,catalog,major,cash,minimum_peers=30)
        preview['rows'][0]['financial_fcf_assets_candidate_points']=99
        with self.assertRaises(LivePartialScorecardError):
            cashflow_source(market,catalog,major,cash,preview)

    def test_tampered_checkpoint_does_not_become_reconciled_evidence(self):
        market,catalog,major,cash=five_complete()
        preview=score_preview(market,catalog,major,cash,minimum_peers=30)
        code=next(iter(cash['records']))
        cash['records'][code]['years']['2025']['free_cashflow_krw']+=1
        self.assertIsNone(cashflow_source(market,catalog,major,cash,preview))
