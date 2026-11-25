from unittest import TestCase
from unittest.mock import Mock
import pandas as pd

from v3_forward_index_benchmark import index_return, reference, summarize


class IndexBenchmarkTests(TestCase):
    def setUp(self):
        self.entry, self.end = '2026-11-16', '2026-12-16'
        self.frame = pd.DataFrame({'Open':[100,108], 'Close':[102,110]},
                                  index=pd.to_datetime([self.entry,self.end]))

    def test_exact_open_to_close(self):
        self.assertEqual(index_return(self.frame,self.entry,self.end),10)
        self.assertIsNone(index_return(self.frame,'2026-11-17',self.end))
        self.assertIsNone(index_return(pd.concat([self.frame,self.frame]),self.entry,self.end))
        invalid=self.frame.copy()
        invalid.iloc[0,0]=0
        self.assertIsNone(index_return(invalid,self.entry,self.end))

    def test_partial_retry_preserves_prior_return(self):
        reader=Mock(side_effect=[self.frame,RuntimeError('provider outage')])
        observed=reference(self.entry,self.end,reader=reader)
        self.assertEqual(observed,{'KOSPI':10,'KOSDAQ':None})
        reader=Mock(return_value=self.frame)
        self.assertEqual(reference(self.entry,self.end,reader=reader,previous=observed),
                         {'KOSPI':10,'KOSDAQ':10})
        reader.assert_called_once_with('KQ11',self.entry,'2026-12-17')

    def test_market_matched_excess_requires_all_valued_members(self):
        groups={'top3':{'valued_count':3,'market_breakdown':{
            'KOSPI':{'valued_count':2,'observed_mean_return_pct':20},
            'KOSDAQ':{'valued_count':1,'observed_mean_return_pct':-5}}}}
        result=summarize(groups,{'KOSPI':10,'KOSDAQ':-2},self.entry,self.end,self.end)
        g=result['groups']['top3']
        self.assertEqual(g['observed_mean_excess_return_pct_points'],5.6667)
        self.assertEqual(g['markets']['KOSDAQ']['excess_return_pct_points'],-3)
        result=summarize(groups,{'KOSPI':10,'KOSDAQ':None},self.entry,self.end,self.end)
        self.assertIsNone(result['groups']['top3']['observed_mean_excess_return_pct_points'])
        self.assertEqual(result['groups']['top3']['matched_valued_count'],2)
