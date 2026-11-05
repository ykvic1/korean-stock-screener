"""Regression checks for bounded derived MA transition evidence."""
import copy
import unittest

import numpy as np
import pandas as pd

from v3_ma_slope_transition import WEIGHTS, observe, valid_observation
import v3_operational_research_score as ops


def bars():
    days = pd.bdate_range(end='2026-11-06', periods=1000)
    close = np.r_[np.linspace(140.,80.,980), np.linspace(80.,240.,20)]
    return pd.DataFrame({'Open':close,'High':close*1.01,
                         'Low':close*.99,'Close':close,
                         'Volume':np.full(len(close),1000.)},index=days)


class MaSlopeTransitionTests(unittest.TestCase):
    def test_all_six_log_ma_lines_are_bounded_and_requires_previous_decline(self):
        df=bars(); item=observe(df)
        self.assertTrue(valid_observation(item,'2026-11-06'))
        self.assertEqual(item['weekly_asof'],'2026-11-06')
        self.assertTrue(any(x>0 for x in item['parts']['daily'].values()))
        self.assertTrue(any(x>0 for x in item['parts']['weekly'].values()))
        self.assertLessEqual(sum(item['parts']['daily'].values()),2)
        self.assertLessEqual(sum(item['parts']['weekly'].values()),3)
        up=bars();up['Close']=np.linspace(100,200,len(up))
        up['Open']=up['Close'];up['High']=up['Close']*1.01;up['Low']=up['Close']*.99
        rising=observe(up)
        self.assertEqual(sum(rising['parts']['daily'].values()),0)
        self.assertEqual(sum(rising['parts']['weekly'].values()),0)

    def test_unfinished_calendar_week_never_updates_weekly_line(self):
        df=bars();last=observe(df)
        partial=df.iloc[-1:].copy();partial.index=pd.DatetimeIndex(['2026-11-09'])
        partial['Close']=300;partial['Open']=300;partial['High']=303;partial['Low']=297
        extended=observe(pd.concat([df,partial]))
        self.assertEqual(extended['weekly_asof'],last['weekly_asof'])
        self.assertEqual(extended['parts']['weekly'],last['parts']['weekly'])
        self.assertTrue(valid_observation(extended,'2026-11-09'))

    def test_missing_history_is_not_zero_and_tampering_is_rejected(self):
        self.assertIsNone(observe(bars().iloc[-80:]))
        item=observe(bars())
        for altered in (
            lambda o:o['parts']['daily'].__setitem__('20',WEIGHTS['daily']['20']+.01),
            lambda o:o.__setitem__('source_regular_session_verified',True),
            lambda o:o.__setitem__('raw_ohlcv_exported',True),
            lambda o:o.__setitem__('weekly_asof','2026-11-07'),
        ):
            bad=copy.deepcopy(item);altered(bad)
            self.assertFalse(valid_observation(bad,item['asof']))

    def test_operational_score_consumes_only_verified_derived_contract(self):
        df=bars();day='2026-11-06';item=observe(df)
        stock={'price_date':day,'market':'KOSPI','v3_ma_slope_transition':item}
        saved={'code':'000001','name':'test','market':'KOSPI'}
        parts,_=ops.score_issuer(stock,saved,day=day)
        self.assertEqual(parts['momentum']['daily_ma_slope_turn']['available'],2)
        self.assertEqual(parts['momentum']['weekly_ma_slope_turn']['available'],3)
        bad=copy.deepcopy(item);bad['investment_score_certified']=True
        stock['v3_ma_slope_transition']=bad
        parts,_=ops.score_issuer(stock,saved,day=day)
        self.assertIsNone(parts['momentum']['daily_ma_slope_turn'])


if __name__=='__main__':unittest.main()
