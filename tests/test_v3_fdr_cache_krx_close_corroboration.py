"""Official-vs-dated FDR close counts only; preserve source quarantine."""
import json
import unittest
import pandas as pd
from v3_fdr_cache_krx_close_corroboration import (
    compare_dated_cache, unavailable, CacheCorroborationError, SCHEMA,
)


def saved():
    return {
        '000001': {'code':'000001','market':'KOSPI'},
        '0008Z0': {'code':'0008Z0','market':'KOSPI'},
        '200002': {'code':'200002','market':'KOSDAQ'},
        '200003': {'code':'200003','market':'KOSDAQ'},
    }


def frame():
    return pd.DataFrame([
        {'Code':'000001','Market':'KOSPI','Close':100},
        {'Code':'0008Z0','Market':'KOSPI','Close':220.0},
        {'Code':'200002','Market':'KOSDAQ','Close':310},
        {'Code':'200003','Market':'KOSDAQ','Close':350},
    ])


class FDRDatedCloseTests(unittest.TestCase):
    def test_full_market_accurate_count_without_issuer_or_quote_export(self):
        official = {('KOSPI','000001'):100,('KOSPI','0008Z0'):220,
                    ('KOSDAQ','200002'):310,('KOSDAQ','200003'):355}
        result = compare_dated_cache(saved(),official,frame(),'a'*64)
        self.assertEqual(result['schema'],SCHEMA)
        self.assertEqual(result['status'],'observed')
        self.assertEqual(result['counts']['requested'],4)
        self.assertEqual(result['counts']['exact_close'],3)
        self.assertEqual(result['counts']['different_close'],1)
        self.assertFalse(result['regular_close_full_market_corroborated'])
        self.assertFalse(result['historical_ohlcv_regular_session_certified'])
        self.assertFalse(result['krx_nxt_consolidated_volume_certified'])
        self.assertFalse(result['investment_score_or_available_weight_changed'])
        self.assertFalse(result['licensed_krx_data_republished'])
        exported = json.dumps(result)
        for code in saved():
            self.assertNotIn(code, exported)
        for forbidden in ('TDD_CLSPRC', '"price"', '"close"', '"Volume"'):
            self.assertNotIn(forbidden, exported)

    def test_all_official_matches_can_corrobate_one_day_only(self):
        official={('KOSPI','000001'):100,('KOSPI','0008Z0'):220,
                  ('KOSDAQ','200002'):310,('KOSDAQ','200003'):350}
        result=compare_dated_cache(saved(),official,frame(),'c'*64)
        self.assertEqual(result['counts']['exact_close'],4)
        self.assertTrue(result['regular_close_full_market_corroborated'])
        self.assertFalse(result['historical_ohlcv_regular_session_certified'])
        self.assertFalse(result['investment_score_or_available_weight_changed'])

    def test_unknown_and_invalid_prices_do_not_become_zero_matches(self):
        official={('KOSPI','000001'):100,('KOSPI','0008Z0'):220,
                  ('KOSDAQ','200002'):310}
        source=frame().copy()
        source.loc[source.Code == '0008Z0','Close']=float('nan')
        source=source[source.Code != '200002'].reset_index(drop=True)
        result=compare_dated_cache(saved(),official,source,'d'*64)
        self.assertEqual(result['counts']['exact_close'],1)
        self.assertEqual(result['counts']['invalid_public_cache_close'],1)
        self.assertEqual(result['counts']['absent_in_public_cache'],1)
        self.assertEqual(result['counts']['absent_in_official'],1)
        self.assertFalse(result['regular_close_full_market_corroborated'])

    def test_duplicated_market_ticker_rejected_not_summed(self):
        source=pd.concat([frame(),frame().iloc[[0]]],ignore_index=True)
        with self.assertRaisesRegex(CacheCorroborationError,'duplicate_public_cache_market_code'):
            compare_dated_cache(saved(),{},source,'e'*64)

    def test_date_and_identity_policy_remain_strict(self):
        invalid = saved()
        invalid['bad/code']={'code':'bad/code','market':'KOSPI'}
        with self.assertRaisesRegex(CacheCorroborationError,'invalid_saved_cache_corroboration_identity'):
            compare_dated_cache(invalid,{},frame(),'f'*64)
        with self.assertRaisesRegex(CacheCorroborationError,'untrusted_dated_cache_corroboration_inputs'):
            compare_dated_cache(saved(),{},frame(),'invalid')

    def test_unavailable_or_not_requested_never_certify(self):
        for status in ('not_requested','dated_cache_unavailable'):
            result=unavailable(status)
            self.assertEqual(result['status'],status)
            self.assertFalse(result['regular_close_full_market_corroborated'])
            self.assertFalse(result['licensed_krx_data_republished'])
            self.assertFalse(result['investment_score_or_available_weight_changed'])
            self.assertNotIn('counts',result)


if __name__=='__main__':
    unittest.main()
