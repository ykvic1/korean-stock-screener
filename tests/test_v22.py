import unittest
import numpy as np
import pandas as pd
from technical_v22 import rsi, indicators, divergence
from quant_engine_v22 import StudyCase,analyze_nearest

class V22Tests(unittest.TestCase):
    def test_wilder_known_example(self):
        prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]
        out=rsi(prices);self.assertAlmostEqual(out[14],70.464135,places=5);self.assertAlmostEqual(out[15],66.249619,places=5)
    def test_flat_and_one_direction(self):
        self.assertEqual(rsi(np.ones(30))[-1],50);self.assertEqual(rsi(np.arange(30))[-1],100);self.assertEqual(rsi(-np.arange(30))[-1],0)
    def test_all_technical_ignore_future(self):
        rng=np.random.default_rng(7);c=100*np.exp(np.cumsum(rng.normal(0,.02,800)))
        df=pd.DataFrame({'Open':c,'High':c*1.01,'Low':c*.99,'Close':c,'Volume':rng.integers(1,1000,800)},index=pd.bdate_range('2020-01-01',periods=800))
        full=indicators(df);prefix=indicators(df.iloc[:703])
        for k in full:np.testing.assert_allclose(full[k][:703],prefix[k],equal_nan=True,err_msg=k)
    def test_divergence_requires_confirmation(self):
        c=np.array([10,9,8,7,8,9,10,9,8,6,8,9,10.]);r=np.array([50,45,40,30,40,50,60,50,45,35,45,50,60.])
        bull,_=divergence(c,r,span=2);self.assertFalse(bull[9]);self.assertTrue(bull[11])
    def test_no_500_cap_and_audit(self):
        cases=[StudyCase(str(i%20+1).zfill(6),'KOSPI',f'{2010+i%15}-01-01',str(i%30),[0]*7,1,1,2,1,False,False,0) for i in range(650)]
        s=analyze_nearest('999999',np.zeros(7),'KOSPI',cases,'test',.4,.1,.07)
        self.assertEqual(s['sample_n'],650);self.assertEqual(s['cap500_audit']['cap500_n'],500)
        self.assertEqual(sum(s['year_distribution'].values()),650)
if __name__=='__main__':unittest.main()

class FinanceAndTradeTests(unittest.TestCase):
    def test_same_day_disclosure_excluded(self):
        from official_data_v22 import receipt_visible
        self.assertFalse(receipt_visible({'rcept_no':'20261014000001'},'2026-10-14'))
        self.assertTrue(receipt_visible({'rcept_no':'20261013000001'},'2026-10-14'))
    def test_ambiguous_and_nonwon_financials_unknown(self):
        from financial_metrics import metrics
        rows=[{'account_id':'ifrs-full_Revenue','currency':'KRW','thstrm_amount':'100'},{'account_id':'ifrs-full_Revenue','currency':'KRW','thstrm_amount':'200'},{'account_id':'dart_OperatingIncomeLoss','currency':'USD','thstrm_amount':'10'}]
        v=metrics(rows)['values'];self.assertIsNone(v['revenue']);self.assertIsNone(v['operating_income']);self.assertIsNone(v['operating_margin_pct'])
    def test_zero_volume_future_no_assumed_fill(self):
        from quant_engine_v22 import label_future
        df=pd.DataFrame({'Open':[100]*6,'High':[102]*6,'Low':[99]*6,'Close':[101]*6,'Volume':[100,0,100,100,100,100]})
        self.assertIsNone(label_future(df,0,5,.1,.05,.4))
