import unittest
import numpy as np
import test_quant_engine as tq
from quant_engine import label_future, chronological_holdout, StudyCase
from official_data import receipt_visible

class BiasTests(unittest.TestCase):
    def test_full_horizon_mfe_after_exit(self):
        df=tq.QuantTests().mk([['2026-01-01',100,100,100,100,100],['2026-01-02',100,110,99,105,100],['2026-01-05',105,180,50,100,100]])
        x=label_future(df,0,2,.05,.05,.4)
        self.assertTrue(x['win_target_first']);self.assertAlmostEqual(x['return_pct'],4.6)
        self.assertAlmostEqual(x['mfe_pct'],80);self.assertAlmostEqual(x['mae_pct'],50)
    def test_gap_stop_worse_than_barrier(self):
        df=tq.QuantTests().mk([['2026-01-01',100,100,100,100,100],['2026-01-02',100,102,99,101,100],['2026-01-05',80,85,79,82,100]])
        x=label_future(df,0,2,.1,.05,.4)
        self.assertEqual(x['gap_count'],1);self.assertAlmostEqual(x['return_pct'],-20.4)
    def test_target_gap_open_precedes_later_intraday_stop(self):
        df=tq.QuantTests().mk([
            ['2026-01-01',100,100,100,100,100],
            ['2026-01-02',100,102,99,101,100],
            ['2026-01-05',115,120,80,90,100]])
        x=label_future(df,0,2,.1,.05,.4)
        self.assertTrue(x['win_target_first'])
        self.assertFalse(x['hit_stop_first'])
        self.assertAlmostEqual(x['return_pct'],14.6)
        self.assertEqual(x['outcome_end_date'],'2026-01-05')

    def test_train_outcomes_on_or_after_split_and_unknown_end_are_purged(self):
        cases=[]
        for i,end in enumerate(('2026-01-02','2026-01-05','2026-01-06',None,'2026-01-08'),1):
            cases.append(StudyCase('000001','KOSPI',f'2026-01-0{i}','q',
                [0.0]*7,2,2,3,4,False,False,0,outcome_end_date=end))
        out=chronological_holdout(cases)
        self.assertEqual(out['split_date'],'2026-01-05')
        self.assertEqual(out['train_n'],1)
        self.assertEqual(out['test_n'],1)
        self.assertEqual(out['purged_training_outcome_overlap_or_unknown_n'],3)
        self.assertFalse(out['independently_fitted_model'])

    def test_disclosure_cutoff(self):
        self.assertTrue(receipt_visible({'rcept_no':'20260101000001'},'2026-01-02'))
        self.assertFalse(receipt_visible({'rcept_no':'20260103000001'},'2026-01-02'))
        self.assertFalse(receipt_visible({},'2026-01-02'))
