import copy
import json
import unittest

import v3_actual_money_structure as m


class MoneyStructureTests(unittest.TestCase):
    def inputs(self):
        # Newest first: the last 240 calendar weeks and 96 five-day cohorts.
        # Smooth non-overlapping growth across short and long windows.
        daily=[100.*1.02**(96-i) for i in range(96)]
        weekly=[100.*1.005**(240-i) for i in range(240)]
        cohorts=[{'money_pressure_intrabucket_bp':3000,
                  'intrabucket_price_change_bp':100 if i%2==0 else -100,
                  'total_actual_amount_100k_krw':200 if i%2==0 else 100}
                 for i in range(96)]
        return daily,weekly,cohorts

    def test_separate_growth_and_twelve_quality_points_without_raw_amounts(self):
        d,w,c=self.inputs();r=m.derive(d,w,c)
        self.assertTrue(m.valid(r))
        self.assertLessEqual(r['growth6'],6)
        self.assertEqual(set(r['components']),set(m.COMPONENT_MAX))
        self.assertGreater(r['components']['persistence'],0)
        self.assertGreater(r['components']['up_down_asymmetry'],0)
        self.assertGreater(r['components']['correction_contraction'],0)
        self.assertNotIn('amount_100k',str(r))
        self.assertNotIn('"ratio":',json.dumps(r))
        self.assertFalse(r['investment_score_certified'])

    def test_missing_source_is_not_a_zero_contribution(self):
        d,w,c=self.inputs()
        self.assertIsNone(m.derive(d[:95],w,c))
        self.assertIsNone(m.derive(d,w[:239],c))
        bad=copy.deepcopy(c);bad[0]['money_pressure_intrabucket_bp']=None
        r=m.derive(d,w,bad)
        self.assertIsNone(r['components']['up_down_asymmetry'])
        self.assertTrue(m.valid(r))

    def test_one_sided_short_window_uses_actual_120_or_240_session_sample(self):
        d,w,c=self.inputs()
        for n in (12,24):
            with self.subTest(one_sided_cohorts=n):
                observed=copy.deepcopy(c)
                for row in observed[:n]:row['intrabucket_price_change_bp']=100
                result=m.derive(d,w,observed)
                self.assertIsNotNone(result['components']['correction_contraction'])
                self.assertEqual(result['correction_window_cohorts'],2*n)
                self.assertTrue(m.valid(result))
                baseline=m.derive(d,w,c)
                for key in m.COMPONENT_MAX:
                    if key!='correction_contraction':
                        self.assertEqual(result['components'][key],baseline['components'][key])
        baseline=m.derive(d,w,c)
        self.assertEqual(baseline['correction_window_cohorts'],12)
        self.assertEqual(baseline['components']['correction_contraction'],1)

    def test_missing_or_one_sided_long_history_never_becomes_zero_or_complete(self):
        d,w,c=self.inputs()
        for row in c:row['intrabucket_price_change_bp']=100
        for defect in ('one_sided','missing','boolean','short','zero_up_baseline'):
            sample=copy.deepcopy(c)
            if defect=='missing':sample[10]=None
            elif defect=='boolean':sample[10]['intrabucket_price_change_bp']=True
            elif defect=='short':sample=sample[:23]
            elif defect=='zero_up_baseline':
                for i,row in enumerate(sample):
                    row['intrabucket_price_change_bp']=100 if i%2 else -100
                    row['total_actual_amount_100k_krw']=0
            result=m.derive(d,w,sample)
            self.assertIsNone(result['components']['correction_contraction'])
            self.assertIsNone(result['correction_window_cohorts'])
        legacy=m.derive(*self.inputs());legacy.pop('correction_window_cohorts')
        self.assertTrue(m.valid(legacy))
        forged=m.derive(*self.inputs());forged['correction_window_cohorts']=1
        self.assertFalse(m.valid(forged))
        # A measured failure of contraction is a genuine observed zero,
        # different from a missing or one-sided comparison sample.
        d,w,c=self.inputs()
        for row in c:
            if row['intrabucket_price_change_bp']<0:row['total_actual_amount_100k_krw']=500
        measured=m.derive(d,w,c)
        self.assertEqual(measured['components']['correction_contraction'],0)
        self.assertEqual(measured['correction_window_cohorts'],12)

    def test_concentration_no_double_credit_on_flat_or_spiky_money(self):
        d,w,c=self.inputs()
        base=m.derive(d,w,c)
        flat=m.derive([100.]*96,[100.]*240,c)
        self.assertEqual(flat['growth6'],0)
        self.assertEqual(flat['components']['concentration_quality'],0)
        spike=copy.deepcopy(d)
        spike[0]=max(spike[:4])*4
        quality=m.derive(spike,w,c)
        self.assertLess(quality['components']['concentration_quality'],
                        base['components']['concentration_quality'])

    def test_invalid_or_forged_provenance_fails_closed(self):
        r=m.derive(*self.inputs())
        for mutate in (
            lambda x:x['components'].__setitem__('persistence',6),
            lambda x:x.__setitem__('original_amounts_or_paths_exported',True),
            lambda x:x.__setitem__('investment_score_certified',True),
            lambda x:x.__setitem__('growth6',float('nan')),
        ):
            changed=copy.deepcopy(r);mutate(changed)
            self.assertFalse(m.valid(changed))


if __name__=='__main__':unittest.main()
