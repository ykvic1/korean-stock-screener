"""Manual last-close recovery never fabricates new weighted observations."""
from copy import deepcopy
import unittest
import pandas as pd

from v3_saved_close_ma_recovery import (
    availability, apply, restrict_to_corroborated_close, select_targets, RecoveryError,
)
from v3_ma_slope_transition import SCHEMA
from v3_structural_stop_reference import observe as observe_stop


DAY = '2026-11-09'


def receipt(daily, weekly):
    return {'schema':SCHEMA,'asof':DAY,'weekly_asof':'2026-11-06' if weekly else None,
            'parts':{'daily':daily,'weekly':weekly},
            'source_regular_session_verified':False,
            'source_corporate_action_adjusted_verified':False,
            'raw_ohlcv_exported':False,'investment_score_certified':False}


def stock(code, old=None, price_date=DAY):
    return {'code':code,'price_date':price_date,'price':8990,
            'v3_ma_slope_transition':old}


def inputs():
    market={'date':DAY,'stocks':[stock('000001'),stock('000002'),stock('000003'),
                                  stock('000004',price_date='2026-11-06')]}
    report={'asof':DAY,'source_digest':'source1','rows':{
        '000001':{'available':89.3},'000002':{'available':92.},
        '000003':{'available':100.},'000004':{'available':88.}}}
    priority={'asof':DAY,'listed_total':4,'operational_source_digest':'source1',
              'coverage_90_first':{'family_conditional_priority_codes':{
                  'price_chart_windows':['000001','000004']}}}
    return market,priority,report


def stop_reference():
    return observe_stop(pd.DataFrame({
        'Low': [8500.] * 60, 'High': [9200.] * 60,
        'Close': [8990.] * 60, 'Volume': [100000.] * 60,
    }), asof=DAY)


class SavedCloseMARecoveryTests(unittest.TestCase):
    def test_only_saved_day_needy_90_first_and_followup(self):
        self.assertEqual(select_targets(*inputs()),
                         ((['000001'],['000002']),
                          {'below_90_priority':1,'already_90_priority':1,
                           'unscored_stop_only_deferred':0}))

    def test_corroborated_price_gate_excludes_different_and_unknown_closes(self):
        from tests.test_v3_krx_saved_close_crosscheck import Provider, saved
        from v3_krx_saved_close_crosscheck import check

        market = saved()
        listing = pd.DataFrame([
            {'Code': '000001', 'Market': 'KOSPI', 'Close': 100},
            {'Code': '000002', 'Market': 'KOSPI', 'Close': 220},
            {'Code': '000003', 'Market': 'KOSDAQ GLOBAL', 'Close': 300},
        ])
        digest, listing_sha = 'a'*64, 'b'*64
        check_result = check(market, [], [], Provider(), snapshot_sha256=digest,
                             dated_cache_loader=lambda day: (listing, listing_sha))
        cohorts = (['000002', '000001'], ['000003'])
        self.assertEqual(
            restrict_to_corroborated_close(
                cohorts, market, check_result, digest, listing, listing_sha),
            (['000001'], ['000003']))
        for bad_digest, bad_listing_sha in (('c'*64, listing_sha), (digest, 'c'*64)):
            with self.assertRaises(RecoveryError):
                restrict_to_corroborated_close(
                    cohorts, market, check_result, bad_digest, listing, bad_listing_sha)

    def test_report_source_mismatch_is_not_trusted(self):
        market,priority,report=inputs()
        report['source_digest']='wrong'
        with self.assertRaises(RecoveryError):select_targets(market,priority,report)

    def test_partial_observation_requires_same_close_and_more_weight(self):
        old=receipt({'20':0.,'60':0.,'120':None},None)
        partial=receipt({'20':0.,'60':0.,'120':None},
                        {'20':0.,'60':None,'120':None})
        market={'date':DAY,'stocks':[stock('000001',deepcopy(old)),stock('000002')]}
        collected={'rows':[
            {'code':'000001','metrics':{'price_date':DAY,'price_latest':8990.,
                                         'v3_ma_slope_transition':partial}},
            {'code':'000002','metrics':{'price_date':DAY,'price_latest':8991.,
                                         'v3_ma_slope_transition':partial}}]}
        result=apply(market,collected)
        self.assertEqual(result['more_observed_receipts'],1)
        self.assertEqual(result['source_close_or_date_mismatch'],1)
        self.assertEqual(result['source_mismatch_fetched_close'],1)
        self.assertEqual(result['source_mismatch_review_codes'],
                         {'fetched_close':['000002']})
        self.assertEqual(availability(market['stocks'][0]['v3_ma_slope_transition'],DAY),
                         (1.1,.6))
        self.assertIsNone(market['stocks'][1]['v3_ma_slope_transition'])

    def test_shorter_new_history_never_erases_valid_longer_previous(self):
        original=receipt({'20':0.,'60':0.,'120':0.},
                         {'20':0.,'60':0.,'120':None})
        shorter=receipt({'20':0.,'60':0.,'120':None},
                        {'20':0.,'60':0.,'120':0.})
        market={'date':DAY,'stocks':[stock('000001',deepcopy(original))]}
        result=apply(market,{'rows':[{'code':'000001','metrics':{
            'price_date':DAY,'price_latest':8990.,'v3_ma_slope_transition':shorter}}]})
        self.assertEqual(result['lost_prior_window'],1)
        self.assertEqual(market['stocks'][0]['v3_ma_slope_transition'],original)

    def test_source_receipts_require_verified_contract(self):
        tampered=receipt({'20':0.,'60':None,'120':None},None)
        tampered['source_regular_session_verified']=True
        with self.assertRaises(RecoveryError):availability(tampered,DAY)

    def test_same_saved_close_reuses_ma_frame_for_unscored_stop(self):
        full=receipt({'20':0.,'60':0.,'120':0.},
                     {'20':0.,'60':0.,'120':0.})
        market={'date':DAY,'stocks':[stock('000001',deepcopy(full))]}
        candidate=stop_reference()
        self.assertEqual(candidate['score_impact'],0)
        updated=apply(market,{'rows':[{'code':'000001','metrics':{
            'price_date':DAY,'price_latest':8990.,
            'v3_ma_slope_transition':full,
            'v3_structural_stop_reference':candidate}}]})
        self.assertEqual(updated.get('more_observed_receipts',0),0)
        self.assertEqual(updated['new_unscored_stop_references'],1)
        self.assertEqual(market['stocks'][0]['v3_ma_slope_transition'],full)
        self.assertEqual(market['stocks'][0]['v3_structural_stop_reference'],candidate)

    def test_stop_reference_never_overwrites_saved_date_or_earned_score(self):
        market={'date':DAY,'stocks':[stock('000001')]}
        bad=stop_reference()
        bad['score_impact']=3
        with self.assertRaises(RecoveryError):
            apply(market,{'rows':[{'code':'000001','metrics':{
                'price_date':DAY,'price_latest':8990.,
                'v3_structural_stop_reference':bad}}]})
        self.assertNotIn('v3_structural_stop_reference',market['stocks'][0])
        checked=apply(market,{'rows':[{'code':'000001','metrics':{
            'price_date':DAY,'price_latest':8991.,
            'v3_structural_stop_reference':stop_reference()}}]})
        self.assertEqual(checked['source_close_or_date_mismatch'],1)
        self.assertEqual(checked['source_mismatch_fetched_close'],1)
        self.assertNotIn('v3_structural_stop_reference',market['stocks'][0])

    def test_date_and_close_mismatches_remain_separate_unscored_rejections(self):
        market={'date':DAY,'stocks':[stock('000001'),stock('000002'),
                                    stock('000003',price_date='2026-11-06')]}
        collected={'rows':[
            {'code':'000001','metrics':{'price_date':'2026-11-06',
                                      'price_latest':8991.}},
            {'code':'000002','metrics':{'price_date':DAY,
                                      'price_latest':8991.}},
            {'code':'000003','metrics':{'price_date':DAY,
                                      'price_latest':8990.}}]}
        result=apply(market,collected)
        self.assertEqual(result['source_close_or_date_mismatch'],3)
        self.assertEqual(result['source_mismatch_fetched_date'],1)
        self.assertEqual(result['source_mismatch_fetched_close'],1)
        self.assertEqual(result['source_mismatch_saved_date'],1)
        self.assertEqual(result['source_mismatch_review_codes'],{
            'saved_date':['000003'], 'fetched_date':['000001'],
            'fetched_close':['000002']})
        self.assertTrue(all('v3_structural_stop_reference' not in item
                            for item in market['stocks']))

    def test_missing_unscored_stop_never_consumes_90_point_source_budget(self):
        market, priority, report=inputs()
        full=receipt({'20':0.,'60':0.,'120':0.},
                     {'20':0.,'60':0.,'120':0.})
        market['stocks'][0]['v3_ma_slope_transition']=full
        market['stocks'][1]['v3_ma_slope_transition']=full
        cohorts,counts=select_targets(market,priority,report)
        self.assertEqual(cohorts,([],[]))
        self.assertEqual(counts,{'below_90_priority':0,'already_90_priority':0,
                                 'unscored_stop_only_deferred':1})

    def test_one_time_legacy_stop_recheck_preserves_score(self):
        market, priority, report=inputs()
        old=stop_reference()
        old.pop('validation_window_bars')
        old.update(low20=None,low60=None,confirmed_swing_low=None,
                   reference_line=None,distance_pct=None,basis=[],
                   confluence_count=0,confidence='unavailable',
                   warning='가격 원천값 무효 또는 결측')
        market['stocks'][0]['v3_structural_stop_reference']=old
        cohorts, counts=select_targets(market,priority,report,repair_legacy_stops=True)
        self.assertEqual(cohorts,(['000001'],[]))
        self.assertEqual(counts['below_90_priority'],1)
        recovered=apply(market,{'rows':[{'code':'000001','metrics':{
            'price_date':DAY,'price_latest':8990.,
            'v3_structural_stop_reference':stop_reference()}}]})
        self.assertEqual(recovered['rechecked_legacy_stop_references'],1)
        self.assertEqual(recovered['newly_usable_reference_lines'],1)
        self.assertEqual(recovered.get('more_observed_receipts',0),0)
        self.assertEqual(select_targets(market,priority,report,
                        repair_legacy_stops=True)[0],([],[]))


if __name__=='__main__':unittest.main()
