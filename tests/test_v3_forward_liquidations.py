"""Test the distinction between a liquidation quote and an executed exit."""
from pathlib import Path
import unittest

from v3_forward_liquidations import OBSERVED, REGISTER, load_liquidations, reviewed_liquidations
from v3_forward_price_replay import replay_price_path


class ForwardLiquidationTests(unittest.TestCase):
    def setUp(self):
        self.code = '008500'
        self.entry, self.split = '2025-11-14', '2025-12-15'
        self.halt, self.first, self.last, self.delisted = (
            '2026-06-16', '2026-06-19', '2026-06-29', '2026-06-30')
        self.sessions = [self.entry, self.split, self.halt, self.first, self.last, self.delisted]
        self.terms = load_liquidations(Path('.'), self.delisted)[self.code].copy()
        self.terms['start_on'] = self.halt
        self.bars = {
            self.entry: self.traded(18750, 1200000),
            self.split: self.traded(3700),
            self.first: self.traded(700),
            self.last: self.traded(624),
        }
        self.notices = {(self.split, self.code): {
            'market': 'KOSPI', 'name': '일정실업', 'action': 'split',
            'shares_before': 1200000, 'shares_after': 6000000, 'old_units': 1, 'new_units': 5,
        }}

    @staticmethod
    def traded(price, shares=6000000):
        return ['KOSPI', '일정실업', 'traded', price, price, price, price, shares]

    def replay(self, days=None, entry_open=18750):
        days = days or self.sessions
        start = max(days[0], self.terms['start_on'])
        return replay_price_path(self.code, days, self.bars, entry_open=entry_open,
                                 notices=self.notices, settlements={(start, self.code): self.terms})

    def test_adjusted_final_close_agrees_with_retained_historical_mark(self):
        self.assertEqual(self.replay(), {'status': OBSERVED, 'exit_session': self.last,
                                         'terminal_return_pct': -83.36})
        self.assertEqual(self.replay(self.sessions[3:], entry_open=700)['status'], OBSERVED)

    def test_last_date_waits_and_a_missing_last_trade_does_not_use_an_earlier_close(self):
        self.assertEqual(self.replay(self.sessions[:-2])['status'], 'liquidation_final_session_pending')
        self.bars[self.last] = ['KOSPI', '일정실업', 'no_reported_volume',
                               None, None, None, None, 6000000]
        self.assertEqual(self.replay()['status'], 'liquidation_final_trade_unavailable')

    def test_archive_and_notice_must_agree_on_the_issuer_and_final_window(self):
        self.bars[self.last][-1] = 6000001
        self.assertEqual(self.replay()['status'], 'liquidation_final_share_count_mismatch')
        self.bars[self.last] = self.traded(624)
        self.bars[self.first][1] = '다른 회사'
        self.assertEqual(self.replay()['status'], 'liquidation_issuer_mismatch')
        self.bars[self.first] = self.traded(700)
        self.bars[self.delisted] = self.traded(625)
        self.assertEqual(self.replay()['status'], 'liquidation_final_date_conflicts_with_archive')

    def test_real_register_checks_date_order_source_and_duplicate_code(self):
        root = Path('.')
        self.assertEqual(len(load_liquidations(root, '2026-11-18')), 5)
        self.assertNotIn(self.code, load_liquidations(root, '2026-06-14'))
        raw = (root / REGISTER).read_bytes()
        with self.assertRaisesRegex(ValueError, 'duplicate_forward_liquidation_record'):
            reviewed_liquidations(raw + raw.splitlines(keepends=True)[1], self.last)
        with self.assertRaisesRegex(ValueError, 'invalid_forward_liquidation_record'):
            reviewed_liquidations(raw.replace(b'2026-06-29', b'2026-06-30'), self.last)
