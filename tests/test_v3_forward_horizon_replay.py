"""Exercise mature horizon orchestration and blocked issuer denominators."""
from datetime import datetime, timezone
from hashlib import sha256
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from v3_forward_horizon_replay import OUT, aggregate_paths, replay_next
from v3_forward_horizon_readiness import HORIZONS
from v3_forward_share_actions import FIELDS, REGISTER
from v3_score_observation_capture import ARCHIVE


class ForwardHorizonReplayTests(unittest.TestCase):
    def test_matured_window_is_published_once_with_unresolved_issuers_retained(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            score_day = '2026-11-13'
            days = ['2026-11-16', '2026-12-16']
            members = {'000001': ['Alpha', 'KOSPI', 50, 1], '000002': ['Beta', 'KOSPI', 30, 2]}
            raw = json.dumps({'rows': members}).encode()
            source = root / ARCHIVE / f'{score_day}.json'
            source.parent.mkdir(parents=True)
            source.write_bytes(raw)
            (root / REGISTER).write_text(','.join(FIELDS) + '\n')
            hashes = {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64}
            readiness = {'cohorts': {score_day: {
                'entry_session_candidate': days[0], 'entry_share_action_review': {},
                'score_observation_sha256': sha256(raw).hexdigest(), 'matched_opening_count': 2,
                'horizons': {str(n): {'status': 'waiting_for_target_session'} for n in HORIZONS},
            }}}
            readiness['cohorts'][score_day]['horizons']['1'] = {
                'status': 'daily_archive_ready_for_path_replay', 'target_session_candidate': days[-1],
                'target_date': days[-1], 'archived_session_count': 2,
            }
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
                report = json.loads((root / OUT / score_day / '1m.json').read_bytes())
                self.assertEqual(report['groups']['all_ranked'], {
                    'selected_count': 2, 'observed_price_path_count': 1,
                    'unresolved_counts': {'missing_traded_bar': 1},
                    'median_terminal_return_pct': 5, 'median_mae_pct': -10,
                    'median_mfe_pct': 10, 'reference_compared_count': 0,
                    'reference_touched_count': 0,
                    'delisting_settlements': {
                        'observed_count': 0,
                        'counts_by_treatment': {},
                        'median_settlement_terminal_return_pct': None,
                        'median_terminal_return_including_settlements_pct': 5,
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

    def test_cash_settlement_only_extends_terminal_return_denominator(self):
        days = ['2026-06-24', '2026-06-25', '2026-06-26', '2026-07-15']
        codes = ('000001', '000002', '000003')
        alphas = ['KOSPI', 'Alpha', 'traded', 100, 110, 90, 105, 1000]
        beta = ['KOSPI', 'Beta', 'traded', 100, 110, 90, 100, 1000]
        panels = {day: {'rows': {codes[0]: alphas}} for day in days}
        for day in days[:2]:
            panels[day]['rows'][codes[1]] = beta
        observation = {'rows': {code: [name, 'KOSPI', 50, rank]
                                for rank, (code, name) in enumerate(zip(codes, ('Alpha', 'Beta', 'Gamma')), 1)}}
        openings = {code: ('official_open_observed', 100) for code in codes[:2]}
        openings[codes[2]] = ('not_traded', None)
        terms = {'market': 'KOSPI', 'name': 'Beta', 'treatment': 'cash_exchange',
                 'halt_on': days[2], 'delisted_on': days[-1], 'record_on': '2026-06-30',
                 'paid_on': days[-1], 'cash_krw_per_old_share': '64'}
        group = aggregate_paths(observation, openings, days, panels, {},
                                settlements={(days[2], codes[1]): terms})['all_ranked']
        self.assertEqual(group['selected_count'], 3)
        self.assertEqual(group['unresolved_counts'], {'not_traded': 1})
        self.assertEqual(group['observed_price_path_count'], 1)
        self.assertEqual(group['median_mae_pct'], -10)
        self.assertEqual(group['delisting_settlements'], {
            'observed_count': 1, 'counts_by_treatment': {'cash_exchange': 1},
            'median_settlement_terminal_return_pct': -36,
            'median_terminal_return_including_settlements_pct': -15.5,
        })
