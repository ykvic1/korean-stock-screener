"""NXT must aggregate its two disjoint sessions, never substitute for KRX."""
import json
import unittest

from v3_nxt_session_volume_probe import (
    NXTProbeError, collect, fetch_session, quantity,
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
    def __init__(self, regular, closing):
        self.calls=[]
        self.responses = [Response({'records':len(regular), 'brdinfoTimeList':regular,
                                    'setTime':DAY+' 20:05'}),
                          Response({'records':len(closing), 'brdinfoEndList':closing})]

    def post(self, url, **kwargs):
        self.calls.append((url,kwargs['data']['scAggDd'],kwargs['data']['pageUnit']))
        return self.responses.pop(0)


class NXTVolumeProbeTests(unittest.TestCase):
    def test_two_nxt_sessions_sum_without_counting_overlap_twice(self):
        s = Session([row('000001','1,000'),row('000002','200','KSQ')],
                    [row('000001','70'),row('000003','30')])
        result=collect(s, DAY)
        self.assertEqual(result['all_nxt_sessions'], {
            ('KOSPI','000001'):1070,('KOSDAQ','000002'):200,('KOSPI','000003'):30})
        self.assertEqual(result['overlap_issuers'],1)
        self.assertEqual(len(s.calls),2)
        self.assertTrue(all(day=='20261111' and count==10000 for _,day,count in s.calls))

    def test_wrong_day_and_duplicate_are_not_silent_zeroes(self):
        with self.assertRaisesRegex(NXTProbeError,'nxt_row_day_unverified'):
            fetch_session(Session([row('000001',1,day='20261110')], []),
                          'regular_market',DAY)
        with self.assertRaisesRegex(NXTProbeError,'nxt_duplicate_market_identity'):
            fetch_session(Session([row('000001',1),row('000001',2)],[]),
                          'regular_market',DAY)

    def test_zero_and_invalid_volumes_are_distinguished(self):
        self.assertEqual(quantity('0'),0)
        for value in (-1,1.5,'2.2','-7',None,True):
            with self.subTest(value=value),self.assertRaises(NXTProbeError):
                quantity(value)

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
