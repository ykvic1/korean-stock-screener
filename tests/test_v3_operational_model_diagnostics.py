import copy
import json
from pathlib import Path
import tempfile
import unittest

import v3_operational_model_diagnostics as d

ROOT=Path(__file__).resolve().parents[1]/'docs'
NAMES=('v3-operational-research-scores.json','v3-operational-research-comparison.json','v22-market-catalog.json')


class ModelDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs=[json.loads((ROOT/n).read_text()) for n in NAMES]

    def test_common_cohort_matches_independent_saved_comparison(self):
        result=d.build(*self.inputs)
        self.assertEqual(result['common_ranked_issuers'],self.inputs[1]['same_input_common_issuers'])
        for n in ('50','100','200'):
            top=result['top'][n]
            self.assertEqual(top['overlap'],self.inputs[1]['top'][n]['overlap'])
            for kind in ('legacy','operational'):
                row=top[kind]
                self.assertEqual(sum(row['capitalization_issuer_counts'].values()),row['issuer_count'])
                self.assertEqual(row['issuer_count'],min(int(n),result['common_ranked_issuers']))
            for part in top['new_model_component_contributions'].values():
                self.assertEqual(part['observed_count']+part['missing_count'],top['operational']['issuer_count'])
        self.assertEqual(result['points_added'],0)
        self.assertEqual(result['provider_calls'],0)
        self.assertFalse(result['performance_validation']['early_rise_detection_validated'])
        self.assertTrue(all(v is None for v in result['performance_validation']['horizon_returns'].values()))

    def test_unknown_capitalization_is_never_classified_as_small(self):
        for value in (None,0,-1,True,float('nan'),'100'):
            self.assertEqual(d.size_band(value),'unknown')
        self.assertEqual(d.size_band(999.9),'under_1000')
        self.assertEqual(d.size_band(1000),'1000_to_5000')
        self.assertEqual(d.size_band(5000),'5000_to_50000')
        self.assertEqual(d.size_band(50000),'50000_plus')

    def test_forged_scores_and_duplicate_legacy_ranks_cannot_drive_diagnostics(self):
        bad=copy.deepcopy(self.inputs)
        next(r for r in bad[0]['rows'].values() if r['score']!=100)['score']=100
        with self.assertRaises(ValueError):d.build(*bad)
        bad=copy.deepcopy(self.inputs)
        ranked=[r for r in bad[0]['rows'].values() if r['rank'] and r['legacy_rank']]
        ranked[1]['legacy_rank']=ranked[0]['legacy_rank']
        with self.assertRaises(ValueError):d.build(*bad)
        bad=copy.deepcopy(self.inputs)
        bad[1]['top']['50']['overlap']+=1
        with self.assertRaises(ValueError):d.build(*bad)

    def test_correlations_do_not_use_missing_as_zero_or_claim_predictive_success(self):
        result=d.build(*self.inputs)
        corr=result['component_correlations']
        self.assertEqual(corr['issuer_count'],sum(r['available']==100 for r in self.inputs[0]['rows'].values()))
        self.assertTrue(corr['low_correlation_does_not_prove_no_double_count'])
        self.assertTrue(corr['cross_sectional_correlation_is_not_predictive_validation'])
        for pair in corr['largest_absolute_correlations']:
            self.assertEqual(pair['issuer_count'],corr['issuer_count'])
            self.assertLessEqual(abs(pair['pearson']),1)

    def test_writer_preserves_last_dated_snapshot_during_catalog_rollover(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in zip(NAMES,self.inputs):
                (root/name).write_text(json.dumps(value))
            d.write(root);before=(root/d.OUTPUT).read_bytes()
            catalog=copy.deepcopy(self.inputs[2])
            from datetime import date,timedelta
            catalog['date']=(date.fromisoformat(catalog['date'])+timedelta(days=1)).isoformat()
            (root/NAMES[2]).write_text(json.dumps(catalog))
            status=d.write(root)
            self.assertEqual(status['status'],'waiting_same_day_capitalization_catalog')
            self.assertEqual((root/d.OUTPUT).read_bytes(),before)

    def test_counterfactual_keeps_same_eligible_universe_and_inputs_immutable(self):
        sources=copy.deepcopy(self.inputs);before=copy.deepcopy(sources)
        result=d.build(*sources)
        risk=result['observed_risk_counterfactual']
        self.assertEqual(risk['positive_deduction_issuer_count'],sum(r['rank'] is not None and r['risk_deduction']>0 for r in sources[0]['rows'].values()))
        self.assertEqual(sum(g['issuer_count'] for g in result['ranked_capitalization_groups'].values()),result['ranked_count'])
        self.assertTrue(risk['ranking_only_not_performance'])
        self.assertEqual(before,sources)


    def test_market_session_exposure_separates_mixed_periods_and_missing(self):
        row = {'parts': {'volume': {'obv_price_divergence':[0,4,'2026-11-25']},
                         'technical': {'daily_rsi':[0,1.2,'2026-11-25']},
                         'momentum': {'daily_ma_slope_turn':[0,2,'2026-11-25']}},
               'regular_close_recomputed_parts':['daily_rsi','daily_ma_slope_turn'],
               'regular_close_ma_periods':['20']}
        result=d.market_session_exposure({'rows':{'000001':row}})
        self.assertEqual(result['maximum_weight'],75)
        self.assertAlmostEqual(result['official_close_recomputed_weight'],1.7)
        self.assertAlmostEqual(result['scope_unverified_weight'],5.5)
        self.assertAlmostEqual(result['missing_weight'],67.8)
        self.assertEqual(result['factors']['momentum:daily_ma_slope_turn']['mixed_source_issuers'],1)
        self.assertFalse(result['volume_turnover_obv_certified'])
        row['regular_close_ma_periods']=['20','60','120']
        row['parts']['momentum']['daily_ma_slope_turn'][1]=.5
        with self.assertRaises(ValueError):
            d.market_session_exposure({'rows':{'000001':row}})

    def test_market_session_exposure_partitions_full_current_roster(self):
        result=d.build(*self.inputs)['market_session_exposure']
        self.assertEqual(result['maximum_weight'],len(self.inputs[0]['rows'])*75)
        self.assertAlmostEqual(result['observed_weight']+result['missing_weight'],
                               result['maximum_weight'],places=3)
        self.assertEqual(result['factors']['volume:obv_price_divergence']['official_close_recomputed_weight'],0)
        self.assertFalse(result['scores_changed'])
