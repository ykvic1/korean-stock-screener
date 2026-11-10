"""Manual last-close recovery never fabricates new weighted observations."""
from copy import deepcopy
import unittest
import pandas as pd

from v3_saved_close_ma_recovery import availability, apply, select_targets, RecoveryError
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
                          {'below_90_priority':1,'already_90_priority':1}))

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
        self.assertNotIn('v3_structural_stop_reference',market['stocks'][0])

    def test_missing_stop_is_first_cohort_only_when_ma_already_full(self):
        market, priority, report=inputs()
        full=receipt({'20':0.,'60':0.,'120':0.},
                     {'20':0.,'60':0.,'120':0.})
        market['stocks'][0]['v3_ma_slope_transition']=full
        market['stocks'][1]['v3_ma_slope_transition']=full
        cohorts,counts=select_targets(market,priority,report)
        self.assertEqual(cohorts,(['000001'],[]))
        self.assertEqual(counts,{'below_90_priority':1,'already_90_priority':0})


if __name__=='__main__':unittest.main()
