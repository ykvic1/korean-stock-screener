"""Price-path cases that a same-session replay cannot exercise."""
import unittest

from v3_forward_price_replay import replay_price_path


class ForwardPriceReplayTests(unittest.TestCase):
    def setUp(self):
        self.sessions = ['2026-11-16', '2026-11-17', '2026-11-18']
        self.bars = dict(zip(self.sessions, [
            ['KOSPI', '테스트', 'traded', 100, 105, 95, 100, 10000],
            ['KOSPI', '테스트', 'traded', 50, 60, 40, 55, 20000],
            ['KOSPI', '테스트', 'traded', 55, 57, 50, 56, 20000],
        ]))
        self.notice = {'market': 'KOSPI', 'name': '테스트', 'action': 'split',
                       'shares_before': 10000, 'shares_after': 20000,
                       'new_units': 2, 'old_units': 1}
        self.notices = {('2026-11-17', '005930'): self.notice}

    def replay(self):
        return replay_price_path('005930', self.sessions, self.bars, entry_open=100,
                                 notices=self.notices, reference=85)

    def test_split_adjusts_each_later_session_and_reference_comparison(self):
        result = self.replay()
        self.assertEqual(result['terminal_return_pct'], 12)
        self.assertEqual(result['mae_pct'], -20)
        self.assertEqual(result['mfe_pct'], 20)
        self.assertEqual(result['first_reference_touch_session'], '2026-11-17')

    def test_change_without_notice_or_session_with_missing_price_withholds_metrics(self):
        self.notices.clear()
        self.assertEqual(self.replay(), {'status': 'change_needs_notice',
                                        'blocked_session': '2026-11-17'})
        self.bars.pop('2026-11-17')
        self.assertEqual(self.replay(), {'status': 'missing_traded_bar',
                                        'blocked_session': '2026-11-17'})

    def test_verified_capital_reduction_still_requires_settlement_evidence(self):
        self.notice['action'] = 'capital_reduction'
        self.assertEqual(self.replay(), {'status': 'share_action_requires_settlement',
                                        'blocked_session': '2026-11-17'})

    def test_holder_neutral_issue_does_not_inflate_return_or_move_the_stop(self):
        self.notice.update(action='bond_conversion', new_units=1, old_units=1)
        result = self.replay()
        self.assertEqual(result['terminal_return_pct'], -44)
        self.assertEqual(result['mae_pct'], -60)
        self.assertEqual(result['mfe_pct'], 5)
        self.assertEqual(result['adjusted_split_sessions'], 0)
        self.assertEqual(result['first_reference_touch_session'], '2026-11-17')
        self.notice['shares_after'] += 1
        self.assertEqual(self.replay(), {'status': 'notice_mismatch',
                                        'blocked_session': '2026-11-17'})

    def test_verified_rename_keeps_held_quantity_and_price_path(self):
        self.notice.update(action='rename', prior_name='이전', name='테스트',
                           shares_after=10000, new_units=1, old_units=1)
        self.bars[self.sessions[0]][1] = '이전'
        for session in self.sessions[1:]:
            self.bars[session][-1] = 10000
        result = self.replay()
        self.assertEqual(result['status'], 'observed_price_path')
        self.assertEqual(result['terminal_return_pct'], -44)
        self.assertEqual(result['adjusted_split_sessions'], 0)
        self.assertEqual(result['first_reference_touch_session'], self.sessions[1])
        self.notices.clear()
        self.assertEqual(self.replay()['status'], 'identity_changed')

    def test_confirmed_interior_no_trade_skips_price_mark_and_requires_traded_exit(self):
        self.notices.clear()
        self.bars[self.sessions[1]] = ['KOSPI', '테스트', 'no_reported_volume',
                                      None, None, None, None, 10000]
        self.bars[self.sessions[2]] = ['KOSPI', '테스트', 'traded', 92, 95, 82, 90, 10000]
        result = self.replay()
        self.assertEqual(result['terminal_return_pct'], -10)
        self.assertEqual(result['mae_pct'], -18)
        self.assertEqual(result['mfe_pct'], 5)
        self.assertEqual(result['first_reference_touch_session'], self.sessions[2])
        self.assertEqual(result['no_trade_session_count'], 1)
        self.bars[self.sessions[2]] = self.bars[self.sessions[1]].copy()
        self.assertEqual(self.replay()['status'], 'evaluation_session_no_reported_volume')

    def test_interior_no_trade_cannot_hide_unverified_share_or_identity_changes(self):
        self.notices.clear()
        row = ['KOSPI', '테스트', 'no_reported_volume', None, None, None, None, 10000]
        day = self.sessions[1]
        for index, val, reason in (
            (1, 'Other', 'identity_changed'),
            (7, None, 'share_count_unavailable'),
            (7, 20000, 'change_needs_notice'),
        ):
            with self.subTest(reason=reason):
                self.bars[day] = row.copy()
                self.bars[day][index] = val
                self.assertEqual(self.replay()['status'], reason)
        self.notices[(day, '005930')] = self.notice
        self.assertEqual(self.replay()['status'], 'share_action_without_trade')
