"""Saved-close 121-session recovery: neither stale data nor fake scores."""
from datetime import date, timedelta
import copy
import unittest

from v3_saved_close_path_recovery import (
    RecoveryError, apply, reconcile_source_receipt, select_targets,
)
from v3_unscored_path_observations import observe

DAY = '2026-11-09'


def receipt():
    days = [date.fromisoformat(DAY)-timedelta(days=120-i) for i in range(121)]
    closes = [100.0]*121
    volume = [1000.0]*121
    volume[60] = 0
    return observe(closes, volume, asof=DAY, session_days=days)


def market():
    return {'date': DAY, 'candidate_count':2,
      'v3_unscored_path_source': {
          'observed_issuer_count':0, 'unavailable_issuer_count':2},
      'stocks': [
        {'code':'000001','price_date':DAY,'price':100.,
         'v3_unscored_path_observations':None,
         'v3_unscored_path_unavailable_reason':'nonpositive_or_invalid_price_volume'},
        {'code':'000002','price_date':DAY,'price':200.,
         'v3_unscored_path_observations':None,
         'v3_unscored_path_unavailable_reason':'nonpositive_or_invalid_price_volume'},
    ]}


def row(code, *, last=100., evidence=None, reason=None):
    return {'code':code,'metrics':{
        'price_date':DAY,'price_latest':last,
        'v3_unscored_path_observations':evidence,
        'v3_unscored_path_unavailable_reason':reason}}


class SavedClosePathRecoveryTests(unittest.TestCase):
    def test_select_only_current_saved_close_and_90_first(self):
        current = market()
        score = {'asof':DAY,'rows':{'000001':{'available':83.5},
                                     '000002':{'available':93.}}}
        self.assertEqual(select_targets(current,score),(['000001'],['000002']))
        current['stocks'][0]['price_date']='2026-11-08'
        self.assertEqual(select_targets(current,score),([],['000002']))
        score['asof']='2026-11-10'
        with self.assertRaises(RecoveryError):
            select_targets(current,score)

    def test_only_exact_source_close_can_restore_real_path(self):
        current=market()
        existing=receipt()
        self.assertIsNotNone(existing)
        copied=copy.deepcopy(existing)
        changes=apply(current,{'rows':[row('000001',evidence=existing),
                                        row('000002',last=199.,evidence=copied)]})
        self.assertEqual(changes['new_observed_paths'],1)
        self.assertEqual(changes['published_close_or_date_mismatch'],1)
        self.assertEqual(current['stocks'][0]['v3_unscored_path_observations'],existing)
        self.assertIsNone(current['stocks'][1]['v3_unscored_path_observations'])
        self.assertEqual(current['stocks'][0]['v3_unscored_path_unavailable_reason'],None)
        self.assertEqual(reconcile_source_receipt(current, 1), 1)
        self.assertEqual(current['v3_unscored_path_source']['observed_issuer_count'], 1)
        self.assertEqual(current['v3_unscored_path_source']['unavailable_issuer_count'], 1)

    def test_recovery_receipt_cannot_silently_hide_preexisting_drift(self):
        current = market()
        apply(current, {'rows': [row('000001', evidence=receipt())]})
        current['v3_unscored_path_source']['observed_issuer_count'] = 1
        current['v3_unscored_path_source']['unavailable_issuer_count'] = 1
        with self.assertRaisesRegex(RecoveryError, 'source_coverage_receipt_mismatch'):
            reconcile_source_receipt(current, 1)

    def test_diagnostic_reclassification_cannot_create_points(self):
        current=market()
        changes=apply(current,{'rows':[row('000001',evidence=None,
            reason='zero_volume_close_changed_without_trade')]})
        self.assertEqual(changes['still_unavailable'],1)
        self.assertEqual(changes.get('new_observed_paths',0),0)
        self.assertEqual(changes['reclassified_without_points'],1)
        self.assertIsNone(current['stocks'][0]['v3_unscored_path_observations'])

    def test_corrupted_observations_fail_closed(self):
        current=market()
        bad=copy.deepcopy(receipt())
        bad['investment_score_certified']=True
        with self.assertRaises(RecoveryError):
            apply(current,{'rows':[row('000001',evidence=bad)]})
        self.assertIsNone(current['stocks'][0]['v3_unscored_path_observations'])


if __name__ == '__main__':
    unittest.main()
