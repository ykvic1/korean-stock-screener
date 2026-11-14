"""Dated KRX close anchoring and historical continuity regressions."""
from collections import Counter
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

    def test_dated_close_survives_unrelated_ohlcv_gaps(self):
        code = '0008Z0'
        rows = [
            {'ISU_CD': code, 'TDD_CLSPRC': '100', 'CMPPREVDD_PRC': '+1',
             'LIST_SHRS': '1,000', 'TDD_OPNPRC': '0', 'ACC_TRDVOL': '-'},
            {'ISU_CD': '005930', 'TDD_CLSPRC': '0', 'LIST_SHRS': '1,000'},
            {'ISU_CD': '000660', 'TDD_CLSPRC': '100', 'LIST_SHRS': '-'},
        ]
        client = SimpleNamespace(daily_market=lambda market, day: (rows, b'raw'))
        missing = Counter()
        with patch.object(recovery, '_snapshot_market'):
            found = recovery.closes_on(client, 'KOSDAQ', DAY,
                                       {code, '005930', '000660', '035720'}, missing=missing)
        self.assertEqual(found, {code: (100, 1_000, 99)})
        self.assertEqual(missing, {'invalid_close': 1, 'invalid_listed_shares': 1,
                                   'absent_issuer': 1})

    def test_rebases_only_a_matching_fixed_face_consolidation(self):
        dates = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        effective = dates[60]
        records = {effective: {'reference_krw': 500, 'old_face_krw': 100,
                               'new_face_krw': 500}}
        prices = [(100, 1_001, 100)] * 60 + [(500, 200, 500)] * 61
        outcome, receipt = recovery.make_receipt(prices, asof=DAY, sessions=dates,
                                                 face_notices=records)
        self.assertEqual(outcome, 'recovered')
        self.assertEqual(receipt['kind_fixed_face_consolidation_links'], 1)
        self.assertEqual(receipt['prior60_close_change_pct'], 0)
        self.assertEqual(receipt['recent60_close_change_pct'], 0)
        self.assertEqual(recovered_close_momentum(receipt, DAY), 0)

        for bad in ({dates[59]: records[effective]},
                    {effective: {**records[effective], 'reference_krw': 505}},
                    {effective: {**records[effective], 'new_face_krw': 400}}):
            with self.subTest(bad=bad):
                self.assertEqual(recovery.make_receipt(
                    prices, asof=DAY, sessions=dates, face_notices=bad),
                    ('listed_share_change_reference_discontinuous_or_missing', None))

    def test_published_kind_notice_registry_is_valid(self):
        notices, digest = recovery.load_face_notices()
        self.assertEqual(notices['333050']['2026-05-20']['reference_krw'], 6970)
        self.assertRegex(digest, r'^[a-f0-9]{64}$')

    def test_face_notices_accept_kospi_and_syndicated_kosdaq(self):
        notices, _ = recovery.load_face_notices()
        kospi = notices['092220']['2026-05-20']
        kosdaq = notices['424760']['2026-05-20']
        self.assertEqual(kospi['market'], 'KOSPI')
        self.assertEqual(kosdaq['market'], 'KOSDAQ')
        self.assertTrue(recovery.valid_face_notice_source(kospi))
        self.assertTrue(recovery.valid_face_notice_source(kosdaq))
        self.assertFalse(recovery.valid_face_notice_source({**kospi, 'face_evidence_url': ''}))
        self.assertFalse(recovery.valid_face_notice_source({**kosdaq, 'market': 'KOSPI'}))

    def test_exchange_listing_notice_requires_exact_share_counts_and_reference(self):
        notices, _ = recovery.load_face_notices()
        notice = notices['004410']['2026-05-21']
        previous = (158, 394550136, 158)
        current = (1436, 39455013, 1580)
        self.assertTrue(recovery.verified_face_consolidation(previous, current, notice))
        self.assertFalse(recovery.verified_face_consolidation(
            previous, (1436, 39455013, 1585), notice))
        self.assertFalse(recovery.verified_face_consolidation(
            previous, (1436, 39455014, 1580), notice))
        self.assertFalse(recovery.valid_face_notice_source({**notice, 'market': 'KOSDAQ'}))

    def test_bonus_rebases_on_rights_date_only_after_listing_confirmation(self):
        dates = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        rights, listing = dates[45], dates[63]
        notice = {'reference_krw': 250, 'ratio_numerator': 1, 'ratio_denominator': 5,
                  'shares_before': 1000, 'shares_after': 1198, 'listing_date': listing}
        prices = ([(300, 1000, 300)] * 45 + [(250, 1000, 250)] * 18
                  + [(250, 1198, 250)] * 58)
        status, receipt = recovery.make_receipt(
            prices, asof=DAY, sessions=dates, bonus_notices={rights: notice})
        self.assertEqual(status, 'recovered')
        self.assertEqual(receipt['kind_bonus_issue_links'], 1)
        self.assertEqual(receipt['prior60_close_change_pct'], 0)
        self.assertEqual(receipt['recent60_close_change_pct'], 0)
        self.assertEqual(recovered_close_momentum(receipt, DAY), 0)
        for change in ({'listing_date': dates[62]}, {'shares_after': 1199},
                       {'ratio_numerator': 2}, {'reference_krw': 245}):
            with self.subTest(change=change):
                self.assertEqual(recovery.make_receipt(
                    prices, asof=DAY, sessions=dates, bonus_notices={rights: {**notice, **change}}),
                    ('official_reference_discontinuity', None))

    def test_bonus_registry_uses_corrected_listing_date(self):
        notices = recovery.load_bonus_notices()
        self.assertEqual(notices['368970']['2026-06-19']['listing_date'], '2026-07-08')
        self.assertEqual(notices['018620']['2026-06-12']['listing_date'], '2026-07-06')

    def test_auction_reduction_preserves_price_discovery(self):
        sessions = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        day = sessions[60]
        notice = {'valuation_krw': 300, 'lower_krw': 150, 'upper_krw': 450,
                  'shares_before': 3000, 'shares_after': 1000, 'ratio': 3}
        prices = [(100, 3000, 100)] * 60 + [(250, 1000, 270)] + [(250, 1000, 250)] * 60
        outcome, receipt = recovery.make_receipt(
            prices, asof=DAY, sessions=sessions, auction_notices={day: notice},
            auction_openings={day: 270})
        self.assertEqual(outcome, 'recovered')
        self.assertEqual(receipt['kind_auction_capital_reduction_links'], 1)
        self.assertAlmostEqual(receipt['prior60_close_change_pct'], -100 / 6, places=5)
        for opening in (None, 250, 460):
            with self.subTest(opening=opening):
                self.assertEqual(recovery.make_receipt(
                    prices, asof=DAY, sessions=sessions, auction_notices={day: notice},
                    auction_openings={day: opening}),
                    ('listed_share_change_reference_discontinuous_or_missing', None))

    def test_auction_notices_have_exact_exchange_evidence(self):
        notices = recovery.load_auction_notices()
        self.assertEqual(notices['109960']['2026-06-01']['ratio'], 20)
        self.assertEqual(notices['036090']['2026-05-21']['shares_after'], 39464095)

    def test_split_and_reduction_use_the_holder_conversion_ratio(self):
        sessions = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        day = sessions[60]
        notices = recovery.load_auction_notices()
        for code, effective, previous, current, expected in (
            ('001230', '2026-05-29', (11400, 31800483, 11400), (2050, 155507715, 2545),
             100 * (2050 / 2280 - 1)),
            ('052300', '2026-05-22', (1741, 16244128, 1741), (1555, 16244128, 1400),
             100 * (1555 / 1741 - 1)),
        ):
            with self.subTest(code=code):
                notice = notices[code][effective]
                close, shares, opening = current
                prices = [previous] * 60 + [current] + [(close, shares, close)] * 60
                kwargs = dict(asof=DAY, sessions=sessions, auction_notices={day: notice})
                outcome, receipt = recovery.make_receipt(prices, **kwargs, auction_openings={day: opening})
                self.assertEqual(outcome, 'recovered')
                self.assertAlmostEqual(receipt['prior60_close_change_pct'], expected, places=5)
                self.assertIsNone(recovery.make_receipt(
                    prices, **kwargs, auction_openings={day: opening + 1})[1])

    def test_consolidation_with_bonus_checks_corrected_issuance(self):
        sessions = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        day, listing = sessions[60], sessions[70]
        notice = {**recovery.load_bonus_notices()['309930']['2026-05-27'], 'listing_date': listing}
        before, intermediate, final = (notice[k] for k in (
            'shares_before_consolidation', 'shares_before', 'shares_after'))
        prices = ([(2210, before, 2210)] * 60 + [(3650, intermediate, 2810)]
                  + [(3650, intermediate, 3650)] * 9 + [(3650, final, 3650)] * 51)
        kwargs = dict(asof=DAY, sessions=sessions, bonus_notices={day: notice})
        outcome, receipt = recovery.make_receipt(prices, **kwargs)
        self.assertEqual(outcome, 'recovered')
        self.assertAlmostEqual(receipt['prior60_close_change_pct'], 100 * (3650 / 2810 - 1), places=5)
        prices[70] = (3650, final + 1, 3650)
        self.assertIsNone(recovery.make_receipt(prices, **kwargs)[1])

    def test_verified_trading_resumption_preserves_genuine_price_jump(self):
        sessions = [d.date().isoformat() for d in pd.bdate_range(end=DAY, periods=121)]
        day = sessions[60]
        notice = recovery.load_resumption_notices()['002210']['2026-07-30']
        prices = [(973, 1000, 973)] * 60 + [(1800, 1000, 1946)] + [(1800, 1000, 1800)] * 60
        kwargs = dict(asof=DAY, sessions=sessions, resumption_notices={day: notice},
                      auction_openings={day: 1946})
        outcome, receipt = recovery.make_receipt(prices, **kwargs)
        self.assertEqual(outcome, 'recovered')
        self.assertEqual(receipt['kind_trading_resumption_links'], 1)
        self.assertAlmostEqual(receipt['prior60_close_change_pct'], 100 * (1800 / 973 - 1), places=5)
        for invalid in ((1800, 1001, 1946), (1800, 1000, 1945), (2600, 1000, 1946)):
            with self.subTest(invalid=invalid):
                self.assertIsNone(recovery.make_receipt(prices[:60] + [invalid] + prices[61:], **kwargs)[1])
        prices[90] = (900, 1000, 1800)
        prices[91] = (1800, 1000, 900)
        self.assertEqual(recovery.make_receipt(prices, **kwargs),
                         ('invalid_or_discontinuous_close_history', None))

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
