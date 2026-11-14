"""Dated KRX close anchoring and historical continuity regressions."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

import v3_krx_close_momentum_recovery as recovery
from v3_path_provisional_factors import recovered_close_momentum

DAY = '2026-11-13'


class CloseMomentumRecoveryTests(unittest.TestCase):
    def test_independent_dated_close_anchors_mismatched_saved_price(self):
        official = (100, 1_000, 99)
        self.assertEqual(recovery.close_anchor({'price': 105}, official, 100),
                         'dated_fdr_krx_match')
        self.assertEqual(recovery.close_anchor({'price': 100}, official, None),
                         'saved_close_krx_match')
        self.assertIsNone(recovery.close_anchor({'price': 105}, official, 104))
        self.assertIsNone(recovery.close_anchor({'price': 105}, None, 105))

    def test_missing_or_discontinuous_reference_never_restores_weight(self):
        observations = [(100, 1_000, 100)] * recovery.SESSIONS
        outcome, receipt = recovery.make_receipt(observations, asof=DAY)
        self.assertEqual(outcome, 'recovered')
        self.assertEqual(recovered_close_momentum(receipt, DAY), 0)

        for row, reason in (
            (None, 'incomplete_close_history'),
            ((100, 1_000, None), 'missing_official_previous_close_reference'),
            ((100, 1_000, 99), 'official_reference_discontinuity'),
            ((100, 1_500, 99), 'listed_share_change_reference_discontinuous_or_missing'),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    recovery.make_receipt(observations[:-1] + [row], asof=DAY),
                    (reason, None),
                )

    def test_recovery_leaves_market_price_and_other_evidence_intact(self):
        code = '0008Z0'
        stock = {'code': code, 'market': 'KOSDAQ', 'price': 105, 'price_date': DAY}
        market = {'date': DAY, 'stocks': [stock]}
        report = {'asof': DAY, 'rows': {code: {
            'available': 89, 'parts': {'momentum': {'existing_momentum': [2, 8, DAY]}},
        }}}
        ledger = {'market_asof': DAY, 'rows': {code: {
            'factors': {'momentum': {'available': 8, 'asof': DAY}},
        }}}
        dates = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        listing = pd.DataFrame([{'Code': code, 'Market': 'KOSDAQ GLOBAL', 'Close': 100}])
        client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))
        wrapped = SimpleNamespace(client=client, requests=121)

        with (
            patch.object(recovery, 'candidate_dates', return_value={'dates': dates}),
            patch.object(recovery, 'KRXClient', return_value=client),
            patch.object(recovery, 'RetryingMoneyClient', return_value=wrapped),
            patch.object(recovery, 'closes_on', return_value={code: (100, 1_000, 100)}) as closes,
        ):
            result = recovery.run(market, report, ledger, 'fake-key',
                                  load_listing=lambda day: (listing, 'a' * 64))
        self.assertEqual(closes.call_count, 121)
        self.assertEqual(result['selected'], 1)
        self.assertEqual(result['counts']['recovered_despite_saved_close_mismatch'], 1)
        self.assertEqual(result['dated_fdr_close_source_sha256'], 'a' * 64)
        self.assertEqual(stock['price'], 105)
        receipt = stock.pop(recovery.STOCK_FIELD)
        self.assertEqual(receipt['close_anchor'], 'dated_fdr_krx_match')
        self.assertEqual(recovered_close_momentum(receipt, DAY), 0)
        self.assertEqual(stock, {'code': code, 'market': 'KOSDAQ', 'price': 105,
                                 'price_date': DAY})
