"""Exercise mature horizon orchestration and blocked issuer denominators."""
from datetime import datetime, timezone
from hashlib import sha256
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from v3_forward_horizon_replay import OUT, aggregate_paths, fcf_exclusion_summary, loss_rate_bounds, pages_results, ready_windows, replay_next, return_gap_counts
from v3_forward_horizon_readiness import HORIZONS
from v3_forward_liquidations import FIELDS as LIQUIDATION_FIELDS, REGISTER as LIQUIDATION_REGISTER
from v3_forward_share_actions import BONUS_EX_RIGHTS, BONUS_LISTING, FIELDS, REGISTER
from v3_score_observation_capture import ARCHIVE


class ForwardHorizonReplayTests(unittest.TestCase):
    def test_fcf_alternatives_reuse_outcomes_and_keep_missing_at_the_original_cutoff(self):
        codes = ['000001', '000002', '000003', '000004']
        members = {code: ['Issuer', 'KOSPI'] for code in codes}
        openings = {code: ['official_open_observed', 100] for code in codes}
        outcomes = {
            codes[0]: {'status': 'observed_price_path', 'terminal_return_pct': 6, 'mae_pct': -2},
            codes[1]: {'status': 'missing_traded_bar'},
            codes[2]: {'status': 'observed_delisting_settlement', 'terminal_return_pct': -3},
            codes[3]: {'status': 'observed_price_path', 'terminal_return_pct': 12, 'mae_pct': -8},
        }
        rankings = {'baseline': codes, 'omit_fcf_yield': [codes[0], codes[3], codes[2], codes[1]]}
        observation = {'rows': members, 'source_score_sha256': 'a' * 64}
        with patch('v3_forward_horizon_replay.prepare_fcf', return_value=(rankings, codes)):
            result = fcf_exclusion_summary(observation, openings, outcomes, {})
        base = result['variants']['baseline']['top3']
        variant = result['variants']['omit_fcf_yield']['top3']
        self.assertEqual(base['unvalued_confirmed_openings_count'], 1)
        self.assertEqual(base['observed_mean_return_pct'], 1.5)
        self.assertEqual(variant['observed_mean_return_pct'], 5)
        self.assertEqual(variant['worst_entry_relative_mae_pct'], -8)
        self.assertNotIn('top10', result['variants']['baseline'])
        self.assertNotIn(codes[0], json.dumps(result))

    def test_resumed_stock_counts_in_return_and_loss_denominators(self):
        days = ['2026-11-16', '2026-11-17', '2026-11-18']
        code = '000001'
        bars = [
            ['KOSPI', 'Alpha', 'traded', 100, 110, 95, 105, 1000],
            ['KOSPI', 'Alpha', 'no_reported_volume', None, None, None, None, 1000],
            ['KOSPI', 'Alpha', 'traded', 93, 94, 85, 90, 1000],
        ]
        observation = {'rows': {code: ['Alpha', 'KOSPI', 50, 1]}}
        openings = {code: ['official_open_observed', 100]}
        panels = {day: {'rows': {code: bar}} for day, bar in zip(days, bars)}
        top = aggregate_paths(observation, openings, days, panels, {})['top20']
        self.assertEqual(top['price_paths_with_no_trade_sessions'], 1)
        self.assertEqual(top['median_terminal_return_pct'], -10)
        self.assertEqual(top['terminal_return_coverage']['loss_rate_bounds_pct'], [100, 100])

    def test_bonus_delivery_enters_terminal_coverage_without_unpriced_downside(self):
        days = ['2026-10-20', '2026-10-21', '2026-11-19']
        _, ex_on, listing = days
        code = '000001'
        bars = [
            ['KOSPI', 'Alpha', 'traded', 100, 110, 95, 100, 1000],
            ['KOSPI', 'Alpha', 'traded', 50, 55, 45, 51, 1000],
            ['KOSPI', 'Alpha', 'traded', 55, 59, 49, 57, 2000],
        ]
        base = {'market': 'KOSPI', 'name': 'Alpha', 'shares_before': 1000,
                'ex_on': ex_on, 'listing_on': listing, 'old_units': 1}
        notices = {
            (ex_on, code): {**base, 'action': BONUS_EX_RIGHTS, 'shares_after': 1000, 'new_units': 2},
            (listing, code): {**base, 'action': BONUS_LISTING, 'shares_after': 2000, 'new_units': 1},
        }
        observation = {'rows': {code: ['Alpha', 'KOSPI', 50, 1]}}
        openings = {code: ['official_open_observed', 100]}
        panels = {day: {'rows': {code: bar}} for day, bar in zip(days, bars)}
        top = aggregate_paths(observation, openings, days, panels, notices)['top20']
        self.assertEqual(top['terminal_return_coverage']['valued_count'], 1)
        self.assertEqual(top['median_terminal_return_pct'], 14)
        self.assertEqual(top['price_paths_with_unpriced_bonus_entitlements'], 1)
        self.assertIsNone(top['median_mae_pct'])
        self.assertIsNone(top['median_mfe_pct'])

    def test_matured_window_is_published_once_with_unresolved_issuers_retained(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            score_day = '2026-11-13'
            days = ['2026-11-16', '2026-12-16']
            members = {'000001': ['Alpha', 'KOSPI', 50, 1], '000002': ['Beta', 'KOSPI', 30, 21]}
            raw = json.dumps({'rows': members}).encode()
            source = root / ARCHIVE / f'{score_day}.json'
            source.parent.mkdir(parents=True)
            source.write_bytes(raw)
            (root / REGISTER).write_text(','.join(FIELDS) + '\n')
            hashes = {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64}
            readiness = {'cohorts': {score_day: {
                'entry_session_candidate': days[0], 'entry_share_action_review': {},
                'entry_session_sample': {'representative_score_asof': score_day,
                                         'counts_toward_distinct_entry_sessions': True},
                'score_observation_sha256': sha256(raw).hexdigest(), 'matched_opening_count': 2,
                'horizons': {str(n): {'status': 'waiting_for_target_session'} for n in HORIZONS},
            }}}
            readiness['cohorts'][score_day]['horizons']['1'] = {
                'status': 'daily_archive_ready_for_path_replay', 'target_session_candidate': days[-1],
                'target_date': days[-1], 'archived_session_count': 2,
                'holding_window_sample': {'status': 'selected', 'compared_with_score_asof': None},
            }
            alternate = '2026-11-12'
            # An earlier same-day score must not consume the first replay slot.
            readiness['cohorts'][alternate] = {
                **readiness['cohorts'][score_day],
                'entry_session_sample': {'representative_score_asof': score_day,
                                         'counts_toward_distinct_entry_sessions': False},
            }
            older = '2026-11-10'
            older_cohort = readiness['cohorts'][older] = {
                **readiness['cohorts'][score_day], 'entry_session_candidate': '2026-11-11',
                'entry_session_sample': {'representative_score_asof': older,
                                         'counts_toward_distinct_entry_sessions': True},
            }
            older_cohort['horizons'] = {**older_cohort['horizons'], '1': {
                **older_cohort['horizons']['1'], 'target_session_candidate': '2026-12-11',
                'holding_window_sample': {'status': 'overlapping'},
            }}
            evidence = {'rows': {code: ['official_open_observed', 100] for code in members}}
            storage = Mock()
            storage.get_content.return_value = (gzip.compress(json.dumps(evidence).encode()), None)
            get_storage = Mock(return_value=storage)
            alpha = ['KOSPI', 'Alpha', 'traded', 100, 110, 90, 105, 1000]
            beta = ['KOSPI', 'Beta', 'traded', 100, 110, 90, 105, 1000]
            panels = {days[0]: {'rows': {'000001': alpha, '000002': beta}, 'source_sha256': hashes},
                      days[1]: {'rows': {'000001': alpha}, 'source_sha256': hashes}}
            now = datetime(2026, 12, 18, tzinfo=timezone.utc)
            module = 'v3_forward_horizon_replay'
            accepted = {'krx_market_source_sha256': hashes,
                        'entry_status_counts': {'official_open_observed': 2}}
            with (patch(f'{module}.read_archived_days', return_value=dict.fromkeys(days, hashes)),
                  patch(f'{module}.build', return_value=readiness),
                  patch(f'{module}.completed_sessions', return_value=days),
                  patch(f'{module}.entry_plan'),
                  patch(f'{module}.public_result', return_value=accepted),
                  patch(f'{module}.read_daily', side_effect=lambda day, _: panels[day])):
                result = replay_next(root, now, get_storage)
                self.assertEqual(result['status'], 'price_path_aggregated')
                self.assertEqual(result['score_asof'], score_day)
                report = json.loads((root / OUT / score_day / '1m.json').read_bytes())
                self.assertEqual(report['entry_session_sample'], {
                    'representative_score_asof': score_day,
                    'counts_toward_distinct_entry_sessions': True,
                })
                self.assertEqual(report['holding_window_sample'],
                                 {'status': 'selected', 'compared_with_score_asof': None})
                published = pages_results(root, readiness, now)
                self.assertFalse(published['performance_validated'])
                latest = published['horizons']['1']['latest']
                self.assertEqual(latest.pop('factor_validation'),
                                 {'status': 'frozen_factor_source_unavailable'})
                self.assertEqual(latest.pop('v33_fcf_exclusions'),
                                 {'status': 'frozen_factor_source_unavailable'})
                self.assertIsNone(latest.pop('index_benchmark'))
                summary = latest.pop('v31_summary')
                self.assertEqual(summary['top3']['selected_count'], 1)
                self.assertEqual(summary['top3']['market_breakdown']['KOSPI'],
                                 {'valued_count': 1, 'observed_mean_return_pct': 5})
                self.assertEqual(summary['top3']['observed_win_rate_bounds_pct'], [100, 100])
                self.assertEqual(summary['top30']['observed_win_rate_bounds_pct'], [50, 100])
                self.assertIsNone(summary['top30']['observed_mean_gain_loss_ratio'])
                self.assertEqual(summary['40to60']['valued_count'], 1)
                self.assertEqual(summary['below40']['unvalued_confirmed_openings_count'], 1)
                self.assertEqual(summary['80plus']['selected_count'], 0)
                self.assertIsNone(summary['80plus']['observed_mean_return_pct'])
                self.assertEqual(published['horizons'], {'1': {'published_window_count': 1, 'latest': {
                    'score_asof': score_day, 'entry_session': days[0], 'evaluation_session': days[-1],
                    'confirmed_opening_count': 1, 'valued_count': 1, 'median_terminal_return_pct': 5,
                    'observed_price_path_count': 1, 'median_mae_pct': -10, 'loss_rate_bounds_pct': [0, 0],
                    'return_gap_counts': {'entry_open_unconfirmed': 0, 'evaluation_no_trade': 0,
                                          'missing_price_path': 0, 'liquidation_proxy_only': 0,
                                          'other_unresolved': 0},
                    'same_market_rank_comparison': {
                        'reference_min_rank': 21, 'reference_max_rank': 100,
                        'weight_basis': 'frozen_candidate_market_mix',
                        'markets': {'KOSPI': {
                            'candidate': {'selected_count': 1, 'confirmed_opening_count': 1,
                                          'valued_count': 1, 'negative_return_count': 0},
                            'reference': {'selected_count': 1, 'confirmed_opening_count': 1,
                                          'valued_count': 0, 'negative_return_count': 0},
                        }},
                        'observed_mean_return_spread_pct_points': None,
                        'loss_rate_spread_bounds_pct_points': [-100, 0],
                    },
                }}})
                with patch(f'{module}.index_benchmark.reference', return_value={'KOSPI': 2, 'KOSDAQ': None}) as reader:
                    indexed = pages_results(root, readiness, now, refresh_index=True)
                    benchmark = indexed['horizons']['1']['latest']['index_benchmark']
                    self.assertEqual(benchmark['groups']['top3']['observed_mean_excess_return_pct_points'], 3)
                    self.assertEqual(benchmark['status'], 'partial_secondary_reference')
                    pages_results(root, readiness, now, refresh_index=True)
                    reader.assert_called_once()
                self.assertNotIn('000001', json.dumps(published))
                changed = readiness['cohorts'][score_day]['horizons']['1']
                changed['target_date'] = '2026-12-15'
                self.assertEqual(pages_results(root, readiness, now)['horizons'], {})
                changed['target_date'] = days[-1]
                del readiness['cohorts'][alternate]
                del readiness['cohorts'][older]
                self.assertEqual(report['groups']['all_ranked'], {
                    'selected_count': 2, 'observed_price_path_count': 1,
                    'price_paths_with_no_trade_sessions': 0,
                    'entry_status_counts': {
                        'absent': 0, 'identity_unresolved': 0, 'not_traded': 0,
                        'open_unavailable': 0, 'official_open_observed': 2,
                    },
                    'unresolved_counts': {'missing_traded_bar': 1},
                    'terminal_return_coverage': {
                        'valued_count': 1, 'unvalued_confirmed_openings_count': 1,
                        'negative_return_count': 0, 'loss_rate_bounds_pct': [0, 50],
                    },
                    'median_terminal_return_pct': 5, 'median_mae_pct': -10,
                    'median_mfe_pct': 10, 'reference_compared_count': 0,
                    'reference_touched_count': 0,
                    'delisting_settlements': {
                        'observed_count': 0,
                        'counts_by_treatment': {},
                        'median_settlement_terminal_return_pct': None,
                        'median_terminal_return_including_settlements_pct': 5,
                    },
                    'liquidation_exit_proxies': {
                        'observed_count': 0,
                        'median_final_session_close_proxy_return_pct': None,
                        'actual_execution_validated': False,
                    },
                    'cash_dividends': {
                        'issuers_with_confirmed_payment': 0,
                        'confirmed_payment_events': 0,
                        'entitled_events_awaiting_payment_evidence': 0,
                        'events_with_unlinked_issuer_identity': 0,
                        'median_price_plus_confirmed_dividend_pct': None,
                    },
                })
                self.assertFalse(report['performance_validated'])
                self.assertNotIn('000001', json.dumps(report))
                self.assertEqual(replay_next(root, now, get_storage)['status'], 'no_new_ready_horizons')
                get_storage.assert_called_once()
                (root / LIQUIDATION_REGISTER).write_text(','.join(LIQUIDATION_FIELDS) + '\n'
                    'KOSPI,000002,Beta,2026-12-16,2026-12-16,2026-12-17,1000,'
                    'https://kind.krx.co.kr/external/2026/11/13/000126/20261113000258/68051.htm,,\n')
                self.assertEqual(pages_results(root, readiness, now)['horizons'], {})
                get_storage.assert_called_once()
                panels[days[-1]]['rows']['000002'] = ['KOSPI', 'Beta', 'traded', 65, 68, 59, 60, 1000]
                self.assertEqual(replay_next(root, now, get_storage)['status'], 'price_path_aggregated')
                self.assertIn('1', pages_results(root, readiness, now)['horizons'])
                groups = json.loads((root / OUT / score_day / '1m.json').read_bytes())['groups']
                group = groups['all_ranked']
                self.assertEqual(group['observed_price_path_count'], 1)
                self.assertEqual(group['median_mae_pct'], -10)
                self.assertEqual(group['unresolved_counts'], {})
                self.assertEqual(group['terminal_return_coverage'], {
                    'valued_count': 1, 'unvalued_confirmed_openings_count': 1,
                    'negative_return_count': 0, 'loss_rate_bounds_pct': [0, 50],
                })
                self.assertEqual(group['liquidation_exit_proxies'], {
                    'observed_count': 1,
                    'median_final_session_close_proxy_return_pct': -40,
                    'actual_execution_validated': False,
                })
                comparison = groups['top20']['same_market_rank_comparison']
                self.assertIsNone(comparison['observed_mean_return_spread_pct_points'])
                self.assertEqual(comparison['loss_rate_spread_bounds_pct_points'], [-100, 0])

                outcome_path = root / OUT / score_day / '1m.json'
                valid = json.loads(outcome_path.read_bytes())
                missing_nested = json.loads(json.dumps(valid))
                del missing_nested['groups']['top20']['terminal_return_coverage']['valued_count']
                nonfinite_nested = json.loads(json.dumps(valid))
                nonfinite_nested['groups']['top20']['median_mae_pct'] = float('nan')
                for invalid in (json.dumps({**valid, 'target_date': '2026-12-15'}),
                                json.dumps({**valid, 'performance_validated': True}),
                                json.dumps(missing_nested), json.dumps(nonfinite_nested), '{'):
                    with self.subTest(invalid=invalid[:60]):
                        outcome_path.write_text(invalid, encoding='utf-8')
                        self.assertEqual(pages_results(root, readiness, now)['horizons'], {})
                        self.assertEqual(replay_next(root, now, get_storage)['status'],
                                         'price_path_aggregated')
                        self.assertEqual(json.loads(outcome_path.read_bytes()), valid)
                        self.assertIn('1', pages_results(root, readiness, now)['horizons'])

    def test_cash_settlement_only_extends_terminal_return_denominator(self):
        days = ['2026-06-24', '2026-06-25', '2026-06-26', '2026-07-15']
        codes = ('000001', '000002', '000003')
        alphas = ['KOSPI', 'Alpha', 'traded', 100, 110, 90, 105, 1000]
        beta = ['KOSPI', 'Beta', 'traded', 100, 110, 90, 100, 1000]
        panels = {day: {'rows': {codes[0]: alphas}} for day in days}
        for day in days[:2]:
            panels[day]['rows'][codes[1]] = beta
        observation = {'rows': {code: [name, 'KOSPI', 50, rank] for code, name, rank in
                                zip(codes, ('Alpha', 'Beta', 'Gamma'), (1, 21, 101))}}
        openings = {code: ('official_open_observed', 100) for code in codes[:2]}
        openings[codes[2]] = ('not_traded', None)
        terms = {'market': 'KOSPI', 'name': 'Beta', 'treatment': 'cash_exchange',
                 'halt_on': days[2], 'delisted_on': days[-1], 'record_on': '2026-06-30',
                 'paid_on': days[-1], 'cash_krw_per_old_share': '64'}
        groups = aggregate_paths(observation, openings, days, panels, {},
                                 settlements={(days[2], codes[1]): terms})
        group = groups['all_ranked']
        self.assertEqual(group['selected_count'], 3)
        self.assertEqual(group['unresolved_counts'], {'not_traded': 1})
        self.assertEqual(group['observed_price_path_count'], 1)
        self.assertEqual(group['entry_status_counts']['official_open_observed'], 2)
        self.assertEqual(group['entry_status_counts']['not_traded'], 1)
        self.assertEqual(group['terminal_return_coverage'], {
            'valued_count': 2, 'unvalued_confirmed_openings_count': 0,
            'negative_return_count': 1, 'loss_rate_bounds_pct': [50, 50],
        })
        self.assertEqual(group['median_mae_pct'], -10)
        self.assertEqual(group['delisting_settlements'], {
            'observed_count': 1, 'counts_by_treatment': {'cash_exchange': 1},
            'median_settlement_terminal_return_pct': -36,
            'median_terminal_return_including_settlements_pct': -15.5,
        })
        comparison = groups['top20']['same_market_rank_comparison']
        self.assertEqual(comparison['observed_mean_return_spread_pct_points'], 41)
        self.assertEqual(comparison['loss_rate_spread_bounds_pct_points'], [-100, -100])

    def test_gap_counts_keep_unconfirmed_openings_out_of_unvalued_openings(self):
        group = {'selected_count': 8, 'entry_status_counts': {'official_open_observed': 6},
                 'terminal_return_coverage': {'unvalued_confirmed_openings_count': 4},
                 'liquidation_exit_proxies': {'observed_count': 1},
                 'unresolved_counts': {'not_traded': 1, 'identity_unresolved': 1,
                                       'evaluation_session_no_reported_volume': 1,
                                       'missing_traded_bar': 1, 'change_needs_notice': 1}}
        self.assertEqual(return_gap_counts(group), {
            'entry_open_unconfirmed': 2, 'evaluation_no_trade': 1,
            'missing_price_path': 1, 'liquidation_proxy_only': 1, 'other_unresolved': 1,
        })
        group['liquidation_exit_proxies']['observed_count'] = 2
        with self.assertRaisesRegex(ValueError, 'forward_return_gap_counts_do_not_reconcile'):
            return_gap_counts(group)

    def test_loss_bounds_cover_missing_and_empty_groups_without_rounding_inwards(self):
        self.assertIsNone(loss_rate_bounds(0, 0, 0))
        self.assertEqual(loss_rate_bounds(0, 3, 3), [0, 100])
        self.assertEqual(loss_rate_bounds(1, 1, 3), [33.3333, 66.6667])
        self.assertEqual(loss_rate_bounds(2, 0, 3), [66.6666, 66.6667])

    def test_ready_windows_rank_selected_then_earliest_maturity(self):
        def cohort(entry, *ready):
            horizons = {str(n): {'status': 'waiting_for_target_session'} for n in HORIZONS}
            for months, target, selection in ready:
                horizons[str(months)] = {'status': 'daily_archive_ready_for_path_replay',
                    'target_session_candidate': target, 'holding_window_sample': {'status': selection}}
            return {'entry_session_candidate': entry, 'horizons': horizons,
                    'entry_session_sample': {'counts_toward_distinct_entry_sessions': True}}

        cohorts = {
            '2026-10-29': cohort('2026-10-30', (12, '2027-11-01', 'selected'),
                                 (1, '2026-11-30', 'overlapping')),
            '2026-12-02': cohort('2026-12-03', (2, '2027-02-03', 'selected')),
            '2027-01-13': cohort('2027-01-14', (1, '2027-02-15', 'selected')),
        }
        self.assertEqual(ready_windows(cohorts), [
            ('2026-12-02', 2), ('2027-01-13', 1), ('2026-10-29', 12), ('2026-10-29', 1),
        ])
