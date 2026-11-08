import copy
from datetime import date, timedelta
import gzip
import json
import unittest

import v3_actual_money_structure as structure
import v3_actual_money_participation as money
from v3_actual_money_contract import usable_component_records, usable_records
from v3_operational_research_score import score_issuer
import test_v3_actual_money_structure as structure_tests
from test_v3_actual_money_participation import fixture, week_days
from test_v3_volume_participation import stock


class ComponentObservationTests(unittest.TestCase):
    def test_complete_source_preserves_every_existing_formula(self):
        d, w, c = structure_tests.MoneyStructureTests().inputs()
        old = structure.derive(d, w, c)
        new = structure.derive_components(d, w, c)
        self.assertTrue(structure.valid_components(new))
        self.assertEqual(new['growth6'], old['growth6'])
        for key, maximum in structure.COMPONENT_MAX.items():
            self.assertEqual(new['components'][key], [old['components'][key], maximum])

    def test_missing_older_week_does_not_hide_short_period_observations(self):
        d, w, c = structure_tests.MoneyStructureTests().inputs()
        full = structure.derive_components(d, w, c)
        w[-1] = None
        short = structure.derive_components(d, w, c)
        self.assertIsNone(short['growth6'])
        self.assertEqual(short['components']['persistence'][1], 4.5)
        self.assertIsNone(short['components']['weekly_structure_increment'])
        for key in ('concentration_quality', 'up_down_asymmetry', 'correction_contraction'):
            self.assertEqual(short['components'][key], full['components'][key])
        self.assertLess(short['components']['persistence'][1], 5)
        only_daily = structure.derive_components(d, [], c)
        self.assertEqual(only_daily['components']['persistence'][1], 2.5)
        self.assertEqual(only_daily['components']['concentration_quality'][1], 1)
        self.assertIsNone(only_daily['components']['weekly_structure_increment'])

    def test_zero_missing_and_invalid_flags_remain_distinct(self):
        d, w, c = structure_tests.MoneyStructureTests().inputs()
        flat = structure.derive_components([100.] * 96, [100.] * 240, c)
        self.assertEqual(flat['components']['concentration_quality'], [0, 2])
        missing = structure.derive_components([], [], [])
        self.assertIsNone(missing)
        bad = copy.deepcopy(c)
        bad[0]['money_pressure_intrabucket_bp'] = True
        self.assertIsNone(structure.derive_components(d, w, bad)['components']['up_down_asymmetry'])
        for mutate in (
            lambda x: x['components'].__setitem__('persistence', [0, 6]),
            lambda x: x.update(missing_weight_not_renormalized=False),
            lambda x: x.update(investment_score_certified=True),
            lambda x: x['components'].__setitem__('concentration_quality', [float('nan'), 2]),
        ):
            forged = copy.deepcopy(flat)
            mutate(forged)
            self.assertFalse(structure.valid_components(forged))

    def test_private_missing_long_period_exports_bounded_independent_components(self):
        store, days, forward, catalog = fixture()
        old_week = (date.fromisoformat(money.closed_week_end(catalog['date'])) - timedelta(weeks=150)).isoformat()
        path = money.week_path(old_week)
        data = json.loads(gzip.decompress(store.files[path]))
        data['issuer_week_factors'].pop('KOSPI:000001', None)
        store.files[path] = gzip.compress(json.dumps(data).encode())
        result = money.collect(store, asof=catalog['date'], catalog=catalog, source_revision='a' * 40,
                               days=days, digest='a' * 64, forward_days=forward, week_loader=week_days)
        self.assertEqual(result['eligible_issuers'], 0)
        self.assertEqual(usable_records(result, catalog), {})
        components = usable_component_records(result, catalog)
        self.assertEqual(set(components), {'000001'})
        observed = components['000001']['component_observations']['components']
        self.assertEqual(observed['persistence'][1], 4.5)
        self.assertEqual(observed['concentration_quality'], [0, 2])
        self.assertIsNone(observed['weekly_structure_increment'])
        self.assertEqual(result['component_observation_coverage']['structure_only_issuers'], 1)
        self.assertEqual(sum(result['history_shape_diagnostic']['pair_counts'].values()), 1)
        payload = json.dumps(result)
        for raw_field in ('ACC_TRDVAL', 'total_actual_amount', 'amount_100k_krw', '"ratio"'):
            self.assertNotIn(raw_field, payload)
        for mutate in (
            lambda x: x['component_records']['000001'].update(computed_asof='2027-01-01'),
            lambda x: x.update(component_eligible_issuers=2),
            lambda x: x['component_records']['000001']['component_observations'].update(investment_score_certified=True),
        ):
            forged = copy.deepcopy(result)
            mutate(forged)
            self.assertEqual(usable_component_records(forged, catalog), {})

    def test_operational_components_do_not_require_legacy_six_or_obv_inputs(self):
        d, w, c = structure_tests.MoneyStructureTests().inputs()
        w[-1] = None
        observation = structure.derive_components(d, w, c)
        source = stock()
        day = source['price_date']
        source['volume_ratio'] = None
        row = {'asof': day, 'component_observations': observation}
        parts, _ = score_issuer(source, {}, day=day, money_components=row)
        self.assertEqual(parts['volume']['concentration_quality']['available'], 2)
        self.assertEqual(parts['volume']['persistence']['available'], 4.5)
        self.assertIsNone(parts['volume']['weekly_structure_increment'])
        self.assertIsNone(parts['volume']['long_horizon_growth'])

    def test_history_shapes_never_infer_listing_dates(self):
        for values, reason in (([1, 2], 'complete'), ([1, None], 'tail_only_missing'),
                               ([1, None, 2], 'internal_missing'), ([None, 1], 'recent_missing'),
                               ([None, None], 'no_observed_history')):
            self.assertEqual(money.history_shape(values), reason)

    def test_partial_receipt_cannot_displace_more_complete_existing_evidence(self):
        d, w, c = structure_tests.MoneyStructureTests().inputs()
        old = structure.derive(d, w, c)
        partial_weekly = w[:-1]
        new = structure.derive_components(d, partial_weekly, c)
        source = stock()
        day = source['price_date']
        prior = {'points': .5, 'asof': day, 'weekly_session_end': day, 'r1c_structure': old}
        parts, _ = score_issuer(source, {}, day=day, money=prior,
            money_components={'asof': day, 'component_observations': new})
        self.assertEqual(parts['volume']['persistence']['available'], 5)
        self.assertEqual(parts['volume']['persistence']['points'], old['components']['persistence'])
        self.assertEqual(parts['volume']['weekly_structure_increment']['available'], 2)
        self.assertEqual(parts['volume']['long_horizon_growth']['available'], 6)


if __name__ == '__main__':
    unittest.main()
