from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from v3_forward_cash_dividends import FIELDS, REGISTER, load_dividends, reviewed_dividends
from v3_forward_price_replay import replay_price_path


TERMS = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261104800249'
PAYMENT = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261123000001'


def load_event(*, record='2026-11-18', name='이노션', scheduled='2026-11-20',
               paid='', source=''):
    row = ['KOSPI', '214320', name, record, '225', TERMS, scheduled, paid, source]
    return next(iter(reviewed_dividends((','.join(FIELDS) + '\n' + ','.join(row) + '\n').encode()).values()))


class ForwardCashDividendTests(TestCase):
    sessions = ['2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19',
                '2026-11-20', '2026-11-23']

    def replay(self, event, *, sessions=None, notices=None, split=False):
        sessions = sessions or self.sessions
        bars = {}
        for day in sessions:
            quote = 500 if split and day >= self.sessions[1] else 1000
            shares = 200 if quote == 500 else 100
            bars[day] = ['KOSPI', '이노션', 'traded', quote, quote, quote, quote, shares]
        return replay_price_path('214320', sessions, bars, entry_open=1000,
                                 notices=notices or {}, dividends=[event])

    def test_t_plus_two_uses_observed_trading_sessions(self):
        self.assertEqual(self.replay(load_event(record='2026-11-17'))['pending_dividend_events'], 0)
        self.assertEqual(self.replay(load_event())['pending_dividend_events'], 1)

    def test_scheduled_payment_is_separate_and_later_proof_can_verify_prior_payment(self):
        planned = self.replay(load_event())
        self.assertEqual(planned['pending_dividend_events'], 1)
        self.assertIsNone(planned['price_plus_confirmed_dividend_pct'])
        event = load_event(paid='2026-11-20', source=PAYMENT)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / REGISTER
            path.parent.mkdir()
            path.write_text(','.join(FIELDS) + '\n' + ','.join(event[k] for k in FIELDS) + '\n')
            key = '214320', '2026-11-18'
            unverified = load_dividends(root, '2026-11-20')[key]
            verified = load_dividends(root, '2026-11-23')[key]
        before_proof = self.replay(unverified, sessions=self.sessions[:-1])
        self.assertEqual(before_proof['pending_dividend_events'], 1)
        self.assertIsNone(before_proof['price_plus_confirmed_dividend_pct'])
        before_payment = self.replay(verified, sessions=self.sessions[:-2])
        self.assertEqual(before_payment['pending_dividend_events'], 1)
        after_proof = self.replay(verified, sessions=self.sessions[:-1])
        self.assertEqual(after_proof['confirmed_dividend_events'], 1)
        self.assertEqual(after_proof['price_plus_confirmed_dividend_pct'], 22.5)
        self.assertEqual(after_proof['terminal_return_pct'], 0)

    def test_cash_uses_record_day_units_and_matching_issuer(self):
        notices = {(self.sessions[1], '214320'): {
            'market': 'KOSPI', 'name': '이노션', 'action': 'split',
            'shares_before': 100, 'shares_after': 200, 'new_units': 2, 'old_units': 1,
        }}
        event = load_event(paid='2026-11-20', source=PAYMENT)
        after_split = self.replay(event, split=True, notices=notices)
        self.assertEqual(after_split['terminal_return_pct'], 0)
        self.assertEqual(after_split['price_plus_confirmed_dividend_pct'], 45)
        wrong_issuer = self.replay(load_event(name='다른 발행사', paid='2026-11-20', source=PAYMENT))
        self.assertEqual(wrong_issuer['unlinked_dividend_events'], 1)
        self.assertIsNone(wrong_issuer['price_plus_confirmed_dividend_pct'])

    def test_payment_requires_independent_dated_filing(self):
        for source in ('', TERMS, 'https://example.org/dividend'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, 'invalid_reviewed_dividend_record'):
                    load_event(paid='2026-11-20', source=source)
