"""Regress the dated exchange-session coverage audit."""
from datetime import datetime
import json
import unittest

from v3_nxt_session_volume_probe import (
    KST, NXTProbeError, aggregate, collect, compare_krx_panel, compare_market_share, eligible_day,
    fetch_session, quantity, regular_volume_crosscheck, session_observation,
)


DAY = '2026-11-11'


def row(code, value, market='STK', day='20261111'):
    return {'aggDd':day,'isuSrdCd':'A'+code,'mktId':market,'accTdQty':value}


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self.payload


class Session:
    def __init__(self, regular, closing, basket=()):
        self.calls=[]
        self.responses = [Response({'records':len(regular), 'brdinfoTimeList':regular,
                                    'setTime':DAY+' 20:05'}),
                          Response({'records':len(closing), 'brdinfoEndList':closing}),
                          Response({'records':len(basket), 'bltdinfoList':list(basket)}),
                          Response({'records':1,'page':1,'total':1,'dailyInfoList':[{
                              'aggDd':'20261111','totalAccTdQty':1200,'totalIsuCnt':2,
                              'preAccTdQty':100,'mainAccTdQty':1000,'aftAccTdQty':100}]})]

    def post(self, url, **kwargs):
        data=kwargs['data']
        self.calls.append((url,data.get('scAggDd',data.get('scBeginDe')),data['pageUnit']))
        return self.responses.pop(0)


class NXTVolumeProbeTests(unittest.TestCase):
    def test_two_nxt_sessions_sum_without_counting_overlap_twice(self):
        s = Session([row('0008Z0','1,000'),row('000002','200','KSQ')],
                    [row('0008Z0','70'),row('000003','30')])
        result=collect(s, DAY)
        self.assertEqual(result['regular_plus_closing'], {
            ('KOSPI','0008Z0'):1070,('KOSDAQ','000002'):200,
            ('KOSPI','000003'):30})
        self.assertEqual(result['overlap_issuers'],1)
        self.assertEqual(len(s.calls),4)
        self.assertTrue(all(day=='20261111' for _,day,_ in s.calls))
        self.assertEqual([count for _,_,count in s.calls],[10000,10000,10000,20])
        report=aggregate(result,DAY,None)
        self.assertEqual(report['sessions']['basket']['status'],'empty_undated_response')
        self.assertIsNone(report['regular_plus_closing_issuers_in_saved_universe'])
        self.assertFalse(report['combined_krx_nxt_volume_verified'])
        self.assertNotIn('0008Z0',json.dumps(report))
        self.assertEqual(report['regular_volume_crosscheck']['status'],'matched')

    def test_regular_volume_crosscheck_rejects_duplicate_positive_aggregate(self):
        session=Session([],[])
        response=session.responses[-1]
        session.responses=[response]
        response.payload['records']=2
        response.payload['dailyInfoList']*=2
        result=regular_volume_crosscheck(session,DAY,{('KOSPI','000001'):1200})
        self.assertEqual(result['status'],'unavailable')

    def test_basket_uses_segment_volume_and_rejects_mismatched_day_or_incomplete_count(self):
        basket={**row('000001','200'),'bltdAccTdQty':'30'}
        session=Session([],[],[basket])
        session.responses=[session.responses[2]]
        item=session_observation(session,'basket',DAY)
        self.assertEqual(item['rows'],{('KOSPI','000001'):30})
        for extra, message in (({'aggDd':'20261112'},'nxt_row_day_unverified'), ({},'nxt_incomplete_market_rows')):
            with self.subTest(extra=extra):
                session=Session([],[],[{**basket,**extra}])
                session.responses=[session.responses[2]]
                if not extra:
                    session.responses[0].payload['totalCnt']=2
                with self.assertRaisesRegex(NXTProbeError,message):
                    session_observation(session,'basket',DAY)

    def test_historical_override_and_aftermarket_cutoff(self):
        friday='2026-11-13'
        eligible_day(friday, datetime(2026,11,16,5,10,tzinfo=KST),False)
        eligible_day(friday, datetime(2026,11,16,21,0,tzinfo=KST),True)
        with self.assertRaisesRegex(NXTProbeError,'nxt_session_not_complete'):
            eligible_day('2026-11-16', datetime(2026,11,16,20,59,tzinfo=KST),True)
        with self.assertRaisesRegex(NXTProbeError,'saved_market_day_too_old'):
            eligible_day(friday, datetime(2026,11,25,5,10,tzinfo=KST),False)

    def test_krx_observed_coverage_preserves_mismatched_and_unavailable_rows(self):
        def daily(market, day):
            code, volume = ('000001','0') if market=='KOSPI' else ('000002','100')
            rows=[{'ISU_CD':code,'ISU_NM':'표본','BAS_DD':day.replace('-',''),
                   'TDD_OPNPRC':'100','TDD_HGPRC':'120','TDD_LWPRC':'90','TDD_CLSPRC':'110',
                   'ACC_TRDVOL':volume,'ACC_TRDVAL':'100000','MKTCAP':'10000000'}]
            return rows,json.dumps({'OutBlock_1':rows}).encode()
        report=compare_krx_panel(daily,DAY,{('KOSPI','000001'),('KOSPI','000002'),('KOSDAQ','000003')})
        self.assertEqual(report['nxt_identity_join'], {'volume_zero':1,'market_mismatch':1,'absent':1})
        self.assertFalse(report['krx_accumulated_volume_session_scope_verified'])

    def test_market_share_comparison_keeps_both_denominator_hypotheses(self):
        market_share={'regular_volume':100,'reported_percent':'10.00'}
        krx_only=compare_market_share(900,market_share)
        combined=compare_market_share(1000,market_share)
        self.assertTrue(krx_only['api_plus_nxt_regular_matches'])
        self.assertFalse(krx_only['api_as_combined_denominator_matches'])
        self.assertFalse(combined['api_plus_nxt_regular_matches'])
        self.assertTrue(combined['api_as_combined_denominator_matches'])
        both=compare_market_share(100000,{'regular_volume':1,'reported_percent':0.0})
        self.assertTrue(both['api_plus_nxt_regular_matches'])
        self.assertTrue(both['api_as_combined_denominator_matches'])
        self.assertFalse(krx_only['same_security_universe_independently_verified'])
        self.assertEqual(compare_market_share(0,market_share),{'status':'unavailable'})

    def test_wrong_day_and_duplicate_are_not_silent_zeroes(self):
        with self.assertRaisesRegex(NXTProbeError,'nxt_row_day_unverified'):
            fetch_session(Session([row('000001',1,day='20261110')], []),
                          'regular_market',DAY)
        with self.assertRaisesRegex(NXTProbeError,'nxt_duplicate_market_identity'):
            fetch_session(Session([row('000001',1),row('000001',2)],[]),
                          'regular_market',DAY)
        with self.assertRaisesRegex(NXTProbeError,'nxt_row_identity_invalid'):
            fetch_session(Session([row('000*Z0',1)],[]), 'regular_market',DAY)

    def test_zero_and_invalid_volumes_are_distinguished(self):
        self.assertEqual(quantity('0'),0)
        for value in (-1,1.5,'2.2','-7',None,True):
            with self.subTest(value=value),self.assertRaises(NXTProbeError):
                quantity(value)

    def test_invalid_nxt_identity_emits_only_bounded_shape_counts(self):
        bad=[
            row('000001',3,market='UNSEEN'),
            {'aggDd':'20261111','mktId':'STK','isuSrdCd':'B000002',
             'accTdQty':'17'},
            {'aggDd':'20261111','mktId':'KSQ','isuSrdCd':'A00*0',
             'accTdQty':'23'},
            {'aggDd':'20261111','mktId':'KSQ','accTdQty':'12'},
        ]
        with self.assertRaisesRegex(NXTProbeError,'nxt_row_identity_invalid') as caught:
            fetch_session(Session(bad,[]),'regular_market',DAY)
        shape=caught.exception.shape_counts
        self.assertEqual(shape,{
            'unexpected_market_id':1,
            'missing_or_nonstring_short_code':1,
            'code_missing_A_prefix':1,
            'code_wrong_width':1,
            'code_non_ascii_alphanumeric':2,
        })
        # Never carry any actual NXT row values, market IDs, or ticker strings
        # into the public diagnostic, regardless of which row first failed.
        self.assertNotIn('000001',json.dumps(shape))
        self.assertNotIn('B000002',json.dumps(shape))
        self.assertNotIn('A00*0',json.dumps(shape))
        self.assertEqual(len(shape),5)

    def test_incomplete_page_rejected(self):
        s=Session([row('000001',42)],[])
        s.responses[0].payload['records']=100
        with self.assertRaisesRegex(NXTProbeError,'nxt_incomplete_market_rows'):
            fetch_session(s,'regular_market',DAY)

    def test_no_pre_nxt_date_backfill(self):
        s=Session([],[])
        with self.assertRaisesRegex(NXTProbeError,'nxt_session_not_applicable'):
            fetch_session(s,'regular_market','2025-03-03')
        self.assertEqual(s.calls,[])


if __name__ == '__main__':
    unittest.main()
