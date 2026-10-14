import unittest
import numpy as np
import test_quant_engine as tq
from quant_engine import label_future
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
    def test_disclosure_cutoff(self):
        self.assertTrue(receipt_visible({'rcept_no':'20260101000001'},'2026-01-02'))
        self.assertFalse(receipt_visible({'rcept_no':'20260103000001'},'2026-01-02'))
        self.assertFalse(receipt_visible({},'2026-01-02'))
