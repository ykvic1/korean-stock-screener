"""KRX close crosscheck never silently changes market evidence or scores."""
import json
import unittest

from v3_krx_saved_close_crosscheck import check, current_report, CrosscheckError
from v3_krx_history_collection import HistoryCollectionError
from v3_price_session_capture import capture_window

DAY = '2026-11-10'


def item(code, close, *, day=DAY):
    return {'ISU_CD':code, 'BAS_DD':day.replace('-',''),
            'TDD_OPNPRC':str(close), 'TDD_HGPRC':str(close+5),
            'TDD_LWPRC':str(close-5), 'TDD_CLSPRC':str(close),
            'ACC_TRDVOL':'100'}


class Provider:
    def __init__(self, *, wrong_day=False):
        self.calls=[]
        self.rows={'KOSPI':[item('000001',100),item('000002',220)],
                   'KOSDAQ':[item('000003',300,day='2026-11-09' if wrong_day else DAY)]}

    def daily_market(self, market, day):
        self.calls.append((market,day))
        rows=self.rows[market]
        return rows,json.dumps({'OutBlock_1':rows},ensure_ascii=False).encode()


def saved():
    return {'date':DAY,'candidate_count':3,'stocks':[
        {'code':'000001','market':'KOSPI','price':100,'price_date':DAY},
        {'code':'000002','market':'KOSPI','price':200,'price_date':DAY},
        {'code':'000003','market':'KOSDAQ','price':300,'price_date':DAY},
    ]}


class SavedCloseSourceTests(unittest.TestCase):
    def test_aftermarket_capture_is_exposure_not_regular_session_evidence(self):
        market=saved()
        market.update({'price_feed':'NAVER_FCHART_DAY_UNVERIFIED_SESSION',
                       'price_regular_session_verified':False,
                       'volume_regular_session_verified':False,
                       'aftermarket_close_contamination_possible':True,
                       'generated_kst':'2026-11-10 19:43 KST'})
        market['stocks'][0]['price_feed_observed_at_kst']='2026-11-10 15:35:20 KST'
        market['stocks'][1]['price_feed_observed_at_kst']='2026-11-10 19:40:05 KST'
        market['stocks'][2]['price_feed_observed_at_kst']='2026-11-10 20:04:00 KST'
        report=check(market,['000001'],['000002'],Provider(),snapshot_sha256='f'*64)
        exposure=report['capture_time_exposure']
        self.assertTrue(exposure['price_feed_declared'])
        self.assertEqual(exposure['snapshot_published_window'], 'aftermarket_1600_2000')
        self.assertEqual(exposure['individual_quote_windows']['aftermarket_1600_2000']['different_close'],1)
        self.assertEqual(exposure['individual_quote_windows']['post_regular_pre_aftermarket_1530_1540']['exact_close'],1)
        self.assertEqual(exposure['individual_quote_windows']['after_aftermarket']['exact_close'],1)
        self.assertEqual(sum(v['requested'] for v in exposure['individual_quote_windows'].values()),3)
        self.assertTrue(exposure['aftermarket_capture_is_not_proof_of_trade_session'])
        self.assertEqual(capture_window('2026-11-10 09:20:11 KST'),'regular_market')
        self.assertEqual(capture_window('2026-11-10 15:39:59 KST'),
                         'post_regular_pre_aftermarket_1530_1540')
        self.assertEqual(capture_window('2026-11-10 15:40:00 KST'),
                         'nxt_aftermarket_1540_1600')
        self.assertEqual(capture_window('2026-11-10 15:59:59 KST'),
                         'nxt_aftermarket_1540_1600')
        self.assertEqual(capture_window('2026-11-10 16:00:00 KST'),
                         'aftermarket_1600_2000')
        self.assertEqual(capture_window('2026-11-10 20:00:00 KST'),
                         'after_aftermarket')
        self.assertEqual(capture_window('bad-time'),'unknown')

    def test_claim_of_regular_session_from_fchart_is_rejected(self):
        market=saved()
        market.update({'price_feed':'NAVER_FCHART_DAY_UNVERIFIED_SESSION',
                       'price_regular_session_verified':True,
                       'volume_regular_session_verified':False,
                       'aftermarket_close_contamination_possible':True})
        with self.assertRaises(CrosscheckError):
            check(market,[],[],Provider(),snapshot_sha256='a'*64)

    def test_all_market_and_priority_are_counted_without_ticker_export(self):
        provider=Provider()
        report=check(saved(),['000001'],['000002'],provider,
                     snapshot_sha256='a'*64)
        self.assertEqual(provider.calls,[('KOSPI',DAY),('KOSDAQ',DAY)])
        self.assertEqual(report['counts']['saved_market']['exact_close'],2)
        self.assertEqual(report['counts']['saved_market']['different_close'],1)
        self.assertEqual(report['counts']['below90_ma_priority']['exact_close'],1)
        self.assertEqual(report['counts']['ninety_plus_ma_priority']['different_close'],1)
        profile=report['mismatch_distance_profile']['saved_market']
        self.assertEqual(profile['different_close'],1)
        self.assertEqual(profile['absolute_close_gap_pct']['3_to_10pct'],1)
        self.assertEqual(profile['direction']['saved_below_krx'],1)
        self.assertEqual(profile['krx_daily_high_low_position']['outside_krx_daily_low_high'],1)
        self.assertEqual(profile['saved_equals_other_krx_ohlc']['open'],0)
        self.assertEqual(report['added_available_weight'],0)
        self.assertIs(report['krx_regular_only_volume_verified'],False)
        text=json.dumps(report)
        for code in ('000001','000002','000003'):
            self.assertNotIn(code,text)
        self.assertNotIn('TDD_CLSPRC',text)
        self.assertNotIn('"close":',text)
        self.assertNotIn('"price":',text)

    def test_absent_code_never_becomes_zero_or_valid_close(self):
        provider=Provider()
        provider.rows['KOSDAQ']=[item('000004',350)]
        report=check(saved(),['000003'],[],provider,snapshot_sha256='b'*64)
        self.assertEqual(report['counts']['below90_ma_priority']['absent_in_krx'],1)
        self.assertEqual(report['counts']['saved_market']['exact_close'],1)
        self.assertEqual(report['counts']['saved_market']['different_close'],1)

    def test_cross_date_response_fails_whole_batch(self):
        with self.assertRaises(HistoryCollectionError):
            check(saved(),['000001'],[],Provider(wrong_day=True),
                  snapshot_sha256='c'*64)

    def test_priority_or_snapshot_identity_not_silently_relaxed(self):
        for first,second in [(['000001','000001'],[]),
                             (['000001'],['000001']),
                             (['999999'],[])]:
            with self.assertRaises(CrosscheckError):
                check(saved(),first,second,Provider(),
                      snapshot_sha256='d'*64)

    def test_unchanged_report_avoids_duplicate_live_source_calls(self):
        report=check(saved(),['000001'],['000002'],Provider(),
                     snapshot_sha256='e'*64)
        self.assertTrue(current_report(report,digest='e'*64,day=DAY))
        for update in ({'snapshot_sha256':'f'*64},
                       {'format_revision':1},
                       {'investment_score_certified':True},
                       {'added_available_weight':1}):
            stale={**report,**update}
            self.assertFalse(current_report(stale,digest='e'*64,day=DAY))
        self.assertFalse(current_report(report,digest='e'*64,day='2026-11-09'))


if __name__ == '__main__':
    unittest.main()
