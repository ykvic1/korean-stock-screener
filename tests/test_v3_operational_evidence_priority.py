import copy
import json
from pathlib import Path
import unittest

import v3_operational_evidence_priority as p

ROOT=Path(__file__).resolve().parents[1]/'docs'


class EvidencePriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(n):return json.loads((ROOT/n).read_text())
        cls.report=read('v3-operational-research-scores.json')
        cls.comparison=read('v3-operational-research-comparison.json')
        cls.catalog=read('v22-market-catalog.json')
        cls.legacy=read('v3-near-100-evidence-priority.json')

    def test_all_current_issuers_are_accounted_for_without_added_points(self):
        result=p.build(self.report,self.comparison,self.catalog,self.legacy)
        self.assertEqual(result['available_100_count']+result['incomplete_count'],result['listed_total'])
        self.assertEqual(len(result['single_component_queue'])+len(result['multiple_missing_index']),result['incomplete_count'])
        self.assertEqual(result['points_added'],0)
        self.assertEqual(result['provider_calls'],0)
        for row in result['single_component_queue']:
            self.assertEqual(len(row['gaps']),1)
            self.assertAlmostEqual(row['missing_weight'],100-row['available'],places=5)

    def test_forged_score_or_legacy_coverage_cannot_drive_new_queue(self):
        bad=copy.deepcopy(self.report)
        next(row for row in bad['rows'].values() if row['available']<100)['available']=100
        with self.assertRaises(ValueError):p.build(bad,self.comparison,self.catalog)
        old=copy.deepcopy(self.comparison)
        old['operational_coverage']['available_100_count']+=1
        with self.assertRaises(ValueError):p.build(self.report,old,self.catalog)

    def test_only_confirmed_catalog_short_history_gets_that_reason(self):
        self.assertEqual(p.category('technical','weekly_pattern','insufficient_history',False),
                         'catalog_confirmed_insufficient_history')
        self.assertEqual(p.category('technical','weekly_pattern','priced',False),
                         'chart_window_or_adjustment_review')
        self.assertEqual(p.category('financial','cashflow_fcf_persistence','insufficient_history',True),
                         'original_filing_review_no_blind_refetch')
