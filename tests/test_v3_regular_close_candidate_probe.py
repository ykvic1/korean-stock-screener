"""Bounded public dated daily-close probe: no raw quotes or KRX prices leave memory."""
import json
import unittest

from v3_regular_close_candidate_probe import (
    parse_dated_close, compare_candidate, NaverMobileDailyProbe, SCHEMA,
)


class FakeProbe:
    def __init__(self, returns):
        self.returns = returns
        self.calls = []

    def daily_close(self, code, day):
        self.calls.append((code, day))
        answer = self.returns.get(code)
        if isinstance(answer, Exception):
            raise answer
        return answer


class CandidateTests(unittest.TestCase):
    def test_dated_history_only_not_live_price(self):
        day = '2026-11-10'
        self.assertEqual(parse_dated_close([
            {'localTradedAt': day, 'closePrice': '1,250'},
            {'localTradedAt': '2026-11-11', 'closePrice': '2,000'}], day), 1250)
        self.assertIsNone(parse_dated_close(
            [{'localTradedAt': '2026-11-11', 'closePrice': '2,000'}], day))
        self.assertIsNone(parse_dated_close(
            [{'localTradedAt': day, 'closePrice': '120'},
             {'localTradedAt': day, 'closePrice': '121'}], day))
        self.assertIsNone(parse_dated_close(
            [{'localTradedAt': day, 'closePrice': 'invalid'}], day))
        self.assertEqual(parse_dated_close(
            {'priceInfos': [{'localTradedAt': day, 'closePrice': 1250}]},
            day), 1250)
        self.assertIsNone(parse_dated_close({'closePrice': '1250'}, day))

    def test_probes_only_divergent_saved_closes_and_keeps_count_only(self):
        day='2026-11-10'
        stocks={
            '000001':{'market':'KOSPI','price_date':day,'price':100.0},
            '000002':{'market':'KOSPI','price_date':day,'price':220.0},
            '000003':{'market':'KOSDAQ','price_date':day,'price':300.0},
            '000004':{'market':'KOSDAQ','price_date':day,'price':390.0},
        }
        official={('KOSPI','000001'):110,('KOSPI','000002'):220,
                  ('KOSDAQ','000003'):310,('KOSDAQ','000004'):400}
        probe=FakeProbe({'000001':110,'000003':300,'000004':RuntimeError('timeout')})
        result=compare_candidate(stocks,official,day,probe)
        self.assertEqual(result['schema'],SCHEMA)
        self.assertEqual(result['markets']['KOSPI']['sampled'],1)
        self.assertEqual(result['markets']['KOSPI']['equals_krx'],1)
        self.assertEqual(result['markets']['KOSDAQ']['equals_saved_fchart'],1)
        self.assertEqual(result['markets']['KOSDAQ']['unavailable'],1)
        self.assertEqual(len(probe.calls),3)
        self.assertNotIn(('000002',day),probe.calls)
        self.assertIs(result['regular_session_source_certified'],False)
        self.assertIs(result['investment_score_or_coverage_promoted'],False)
        self.assertIs(result['official_krx_source_data_republished'],False)
        exported=json.dumps(result)
        for code in stocks:
            self.assertNotIn(code,exported)
        for value in ('110','220','300','310','390','400'):
            self.assertNotIn('"price":'+value,exported)

    def test_sample_cap_is_deterministic_and_market_balanced(self):
        day='2026-11-10'
        stocks={f'{i:06}':{'market':'KOSPI' if i<50 else 'KOSDAQ',
                               'price_date':day,'price':100}
                for i in range(100)}
        official={(r['market'],code):110 for code,r in stocks.items()}
        a=FakeProbe({})
        first=compare_candidate(stocks,official,day,a,per_market=6)
        second=FakeProbe({})
        assert compare_candidate(stocks,official,day,second,per_market=6)==first
        self.assertEqual(a.calls,second.calls)
        self.assertEqual(len(a.calls),12)
        self.assertEqual(first['markets']['KOSPI']['sampled'],6)
        self.assertEqual(first['markets']['KOSDAQ']['sampled'],6)
        self.assertEqual(first['markets']['KOSDAQ']['unavailable'],6)
        with self.assertRaises(ValueError):
            compare_candidate(stocks,official,day,a,per_market=500)

    def test_http_candidate_rejects_untrusted_live_and_large_responses(self):
        class Response:
            status_code=200
            content=b'{}'
            def json(self):
                return [{'localTradedAt':'2026-11-10','closePrice':'1,200'}]
        class Session:
            def __init__(self):
                self.calls=[]
                self.response=Response()
            def get(self,url,**kwargs):
                self.calls.append((url,kwargs))
                return self.response
        session=Session()
        p=NaverMobileDailyProbe(session)
        self.assertEqual(p.daily_close('000001','2026-11-10'),1200)
        self.assertEqual(len(session.calls),1)
        self.assertIn('000001/price',session.calls[0][0])
        self.assertIsNone(p.daily_close('abc<script>','2026-11-10'))
        self.assertEqual(len(session.calls),1)
        session.response.status_code=403
        self.assertIsNone(p.daily_close('000001','2026-11-10'))
        session.response.status_code=200
        session.response.content=b'x'*131073
        self.assertIsNone(p.daily_close('000001','2026-11-10'))


if __name__=='__main__':
    unittest.main()
