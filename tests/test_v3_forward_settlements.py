"""Verify reviewed settlement records and outcomes independently of continuous prices."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from v3_forward_price_replay import replay_price_path
from v3_forward_settlements import DISSOLUTION, FIELDS, REGISTER, load_settlements, reviewed_settlements


class ForwardSettlementTests(unittest.TestCase):
    def setUp(self):
        self.sessions = ['2025-11-25', '2025-11-26', '2025-11-27', '2025-11-28',
                         '2025-12-15', '2025-12-16']
        self.halt, self.listing, self.target = self.sessions[2], self.sessions[4], self.sessions[5]
        self.bars = {day: ['KOSPI', 'HD현대미포', 'traded', 100, 110, 95, 103, 1000]
                     for day in self.sessions[:2]}
        self.panels = {
            self.listing: {'rows': {'329180': ['KOSPI', 'HD현대중공업', 'traded', 105, 110, 98, 108, 800]}},
            self.target: {'rows': {'329180': ['KOSPI', 'HD현대중공업', 'traded', 53, 60, 50, 55, 1600]}},
        }
        self.notices = {(self.target, '329180'): {
            'action': 'split', 'name': 'HD현대중공업', 'market': 'KOSPI',
            'shares_before': 800, 'shares_after': 1600, 'old_units': 1, 'new_units': 2,
        }}
        self.terms = load_settlements(Path('.'), '2026-11-18')[(self.halt, '010620')].copy()
        self.terms['successor_shares_on_listing'] = '800'

    def replay(self):
        return replay_price_path('010620', self.sessions, self.bars, entry_open=100,
                                 notices=self.notices, settlements={(self.halt, '010620'): self.terms},
                                 panels=self.panels)

    def test_stock_and_paid_fraction_use_their_own_price_basis(self):
        self.terms.update(shares_per_old_share='1.25', paid_on=self.target)
        self.assertEqual(self.replay(), {'status': 'observed_delisting_settlement',
                                         'treatment': 'stock_exchange', 'terminal_return_pct': 37})
        self.panels[self.listing]['rows']['329180'][6] = 107
        self.assertEqual(self.replay()['status'], 'settlement_fractional_cash_rounding_unconfirmed')
        self.terms['fractional_cash_basis'] = 'listing_close_round_down'
        self.assertEqual(self.replay()['terminal_return_pct'], 36)
        self.terms['fractional_cash_basis'] = 'listing_close_round_half_up'
        self.assertEqual(self.replay()['terminal_return_pct'], 37)
        self.terms['paid_on'] = ''
        self.assertEqual(self.replay()['status'], 'settlement_fractional_cash_unconfirmed')
        self.terms['shares_per_old_share'] = '1'
        self.assertEqual(self.replay()['terminal_return_pct'], 10)

    def test_halt_and_successor_identity_must_match_the_archived_rows(self):
        self.bars[self.halt] = self.bars[self.sessions[0]]
        self.assertEqual(self.replay()['status'], 'settlement_halt_conflicts_with_archive')
        del self.bars[self.halt]
        self.terms['successor_shares_on_listing'] = '801'
        self.assertEqual(self.replay()['status'], 'settlement_successor_listing_unconfirmed')
        self.terms['successor_shares_on_listing'] = '800'
        self.panels[self.listing]['rows']['329180'][1] = '다른 회사'
        self.assertEqual(self.replay()['status'], 'settlement_successor_listing_unconfirmed')

    def test_successor_can_resume_after_an_observed_interior_no_trade_session(self):
        next_day = '2025-12-17'
        self.sessions.append(next_day)
        self.panels[next_day] = self.panels[self.target]
        self.notices[next_day, '329180'] = self.notices.pop((self.target, '329180'))
        self.terms['shares_per_old_share'] = '1'
        halted = ['KOSPI', 'HD현대중공업', 'no_reported_volume', None, None, None, None, 800]
        self.panels[self.target] = {'rows': {'329180': halted}}
        self.assertEqual(self.replay()['terminal_return_pct'], 10)

        halted[-1] = 801
        self.assertEqual(self.replay()['status'], 'settlement_successor_change_needs_notice')
        halted[-1] = 800
        self.panels.pop(next_day)
        self.assertEqual(self.replay()['status'], 'settlement_successor_price_unavailable')
        self.sessions.pop()
        self.assertEqual(self.replay(), {
            'status': 'evaluation_session_no_reported_volume',
            'blocked_session': self.target,
        })

    def test_new_holding_must_have_settled_before_the_record_date(self):
        self.sessions = self.sessions[1:]
        self.terms['record_on'] = self.halt
        self.assertEqual(self.replay()['status'], 'settlement_entry_entitlement_unconfirmed')

    def test_filed_payment_and_compound_event_are_held_separately(self):
        root, cash_key = Path('.'), ('2026-06-26', '012510')
        raw = (root / REGISTER).read_bytes()
        self.assertNotIn(cash_key, load_settlements(root, '2026-08-13'))
        terms = load_settlements(root, '2026-11-18')
        self.assertEqual(terms[cash_key]['cash_krw_per_old_share'], '120000')
        self.assertEqual(date.fromisoformat(terms[cash_key]['paid_on']), date(2026, 7, 15))
        self.assertEqual(terms[('2026-10-23', '097780')]['treatment'], 'review_pending')
        conflicting = raw + raw.splitlines(keepends=True)[1]
        with self.assertRaisesRegex(ValueError, 'duplicate_forward_settlement_record'):
            reviewed_settlements(conflicting)

    def test_dissolution_waits_for_final_payment_and_requires_entry_entitlement(self):
        key = ('2026-04-29', '152550')
        terms = load_settlements(Path('.'), '2026-11-18')[key]
        days = ['2026-04-27', '2026-04-28', key[0], '2026-05-04', '2026-05-08']
        traded = ['KOSPI', '한국ANKOR유전', 'traded', 100, 105, 95, 105, 70020000]
        bars = dict.fromkeys(days[:2], traded)

        def replay(sessions):
            return replay_price_path(key[1], sessions, bars, entry_open=100,
                                     notices={}, settlements={key: terms})

        self.assertEqual(replay(days[:3])['status'], 'dissolution_delisting_pending')
        self.assertEqual(replay(days)['status'], 'dissolution_distribution_unconfirmed')
        terms.update(record_on=days[2], paid_on=days[4], cash_krw_per_old_share='24')
        self.assertEqual(replay(days[:-1])['status'], 'dissolution_distribution_unconfirmed')
        self.assertEqual(replay(days), {'status': 'observed_delisting_settlement',
                                        'treatment': DISSOLUTION, 'terminal_return_pct': -76})
        terms['cash_krw_per_old_share'] = '0'
        self.assertEqual(replay(days)['terminal_return_pct'], -100)
        self.assertEqual(replay(days[1:])['status'], 'settlement_entry_entitlement_unconfirmed')

    def test_dissolution_final_amount_is_visible_only_after_the_official_report(self):
        raw = REGISTER.read_bytes()
        pending = raw.splitlines(keepends=True)[-1]
        values = dict(zip(FIELDS, pending.decode().strip().split(',')))
        values.update(record_on='2026-04-30', cash_krw_per_old_share='0')
        replacement = (','.join(values.values()) + '\n').encode()
        with self.assertRaisesRegex(ValueError, 'invalid_forward_settlement_record'):
            reviewed_settlements(raw.replace(pending, replacement))
        # Hypothetical final report. The published record still has no confirmed amount.
        values.update(paid_on='2026-06-08',
                      payment_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260609000001')
        key = ('2026-04-29', '152550')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / REGISTER).parent.mkdir()
            (root / REGISTER).write_bytes(raw.replace(pending, (','.join(values.values()) + '\n').encode()))
            self.assertEqual(load_settlements(root, '2026-06-08')[key]['cash_krw_per_old_share'], '')
            self.assertEqual(load_settlements(root, '2026-06-09')[key]['cash_krw_per_old_share'], '0')
