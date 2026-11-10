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

    def test_short_daily_history_keeps_observed_20_60_without_unearned_120(self):
        item=observe(bars().iloc[-160:])
        self.assertTrue(valid_observation(item,item['asof']))
        self.assertIsNone(item['parts']['daily']['120'])
        self.assertIsNotNone(item['parts']['daily']['20'])
        self.assertIsNotNone(item['parts']['daily']['60'])
        self.assertIsNone(item['parts']['weekly'])
        stock={'price_date':item['asof'],'market':'KOSPI','v3_ma_slope_transition':item}
        parts,_=ops.score_issuer(stock,{'code':'000001','name':'test','market':'KOSPI'},day=item['asof'])
        self.assertAlmostEqual(parts['momentum']['daily_ma_slope_turn']['available'],1.1)
        self.assertIsNone(parts['momentum']['weekly_ma_slope_turn'])

    def test_short_weekly_history_keeps_20_60_but_not_120(self):
        item=observe(bars().iloc[-520:])
        self.assertTrue(valid_observation(item,item['asof']))
        self.assertEqual(item['weekly_asof'],item['asof'])
        self.assertIsNotNone(item['parts']['weekly']['20'])
        self.assertIsNotNone(item['parts']['weekly']['60'])
        self.assertIsNone(item['parts']['weekly']['120'])
        stock={'price_date':item['asof'],'market':'KOSPI','v3_ma_slope_transition':item}
        parts,_=ops.score_issuer(stock,{'code':'000001','name':'test','market':'KOSPI'},day=item['asof'])
        self.assertAlmostEqual(parts['momentum']['weekly_ma_slope_turn']['available'],1.5)

    def test_older_bad_weekly_candle_only_invalidates_its_long_ma(self):
        df=bars();bad=df.copy()
        bad.loc[bad.index[-650],'High']=bad.loc[bad.index[-650],'Close']*.5
        item=observe(bad)
        self.assertTrue(valid_observation(item,item['asof']))
        self.assertIsNotNone(item['parts']['weekly']['20'])
        self.assertIsNotNone(item['parts']['weekly']['60'])
        self.assertIsNone(item['parts']['weekly']['120'])
        self.assertEqual(item['parts']['daily'],observe(df)['parts']['daily'])

    def test_null_and_all_missing_line_values_are_not_certified(self):
        item=observe(bars().iloc[-160:])
        bad=copy.deepcopy(item)
        bad['parts']['daily']={'20':None,'60':None,'120':None}
        self.assertFalse(valid_observation(bad,item['asof']))
        bad=copy.deepcopy(item)
        bad['parts']['daily']['20']='0'
        self.assertFalse(valid_observation(bad,item['asof']))

    def test_older_bad_weekly_bar_preserves_independent_recent_daily_turn(self):
        df=bars();baseline=observe(df)
        bad=df.copy()
        index=bad.index[-230]
        bad.loc[index,'High']=bad.loc[index,'Close']*.5
        partial=observe(bad)
        self.assertIsNotNone(partial)
        self.assertEqual(partial['parts']['daily'],baseline['parts']['daily'])
        self.assertIsNone(partial['parts']['weekly'])
        self.assertIsNone(partial['weekly_asof'])
        self.assertTrue(valid_observation(partial,'2026-11-06'))

    def test_malformed_recent_price_window_is_not_promoted_to_points(self):
        df=bars();bad=df.copy()
        bad.loc[bad.index[-20],'Low']=bad.loc[bad.index[-20],'Close']*2
        self.assertIsNone(observe(bad))

    def test_older_unrelated_bar_does_not_erase_valid_recent_windows(self):
        df=bars();baseline=observe(df)
        bad=df.copy()
        bad.loc[bad.index[0],'High']=0
        self.assertEqual(observe(bad),baseline)

    def test_historical_no_trade_keeps_last_close_in_ma_without_volume_signal(self):
        df=bars();baseline=observe(df)
        flat=df.copy()
        flat.loc[flat.index[-110],'Volume']=0
        flat.loc[flat.index[-110],['Open','High','Low','Close']]=flat.loc[
            flat.index[-111],'Close']
        result=observe(flat)
        self.assertTrue(valid_observation(result,'2026-11-06'))
        self.assertEqual(result['parts']['daily'].keys(),baseline['parts']['daily'].keys())
        self.assertIsNotNone(result['parts']['weekly'])

    def test_completed_friday_with_no_trade_is_valid_for_monday_weekly_ma(self):
        df=bars()
        df.loc[df.index[-1],['Open','High','Low','Close']]=df.loc[
            df.index[-2],'Close']
        baseline=observe(df)
        df.loc[df.index[-1],'Volume']=0
        monday=df.iloc[-1:].copy()
        monday.index=pd.DatetimeIndex(['2026-11-09'])
        monday['Volume']=1000
        result=observe(pd.concat([df,monday]))
        self.assertTrue(valid_observation(result,'2026-11-09'))
        self.assertEqual(result['weekly_asof'],'2026-11-06')
        self.assertEqual(result['parts']['weekly'],baseline['parts']['weekly'])

    def test_zero_volume_changed_close_is_not_accepted_as_trading_price(self):
        df=bars()
        df.loc[df.index[-50],'Volume']=0
        self.assertIsNone(observe(df))
        latest=bars()
        latest.loc[latest.index[-1],'Volume']=0
        self.assertIsNone(observe(latest))

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
