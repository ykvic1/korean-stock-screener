"""Official-gap recovery cannot drop a market day or change a valid quote."""
from datetime import datetime
import unittest

import pandas as pd

from v3_official_gap_ma_recovery import (
    FIELDS, GAP, KST, NEIGHBORS, OUTER_ANCHORS, add_weekly_ma120, repair, source_frame,
    weekly_position, weekly_rsi, recover_weekly_pattern,
)
from v3_ma_slope_transition import observe
from v3_pattern_first_observations import observe_completed_weekly

DATES = [NEIGHBORS[0], GAP, NEIGHBORS[1]]
TICKER = '000001.KS'


def payload():
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol': TICKER, 'currency': 'KRW', 'exchangeTimezoneName': 'Asia/Seoul',
                 'instrumentType': 'EQUITY'},
        'timestamp': [int(datetime.fromisoformat(d).replace(tzinfo=KST).timestamp()) for d in DATES],
        'indicators': {'quote': [{f.lower(): [100., None, 120.] for f in FIELDS}]},
    }]}}


class OfficialGapMARecoveryTests(unittest.TestCase):
    def test_official_row_only_replaces_all_null_gap(self):
        original = source_frame(payload(), TICKER, DATES,
                                {NEIGHBORS[0]: 100., NEIGHBORS[1]: 120.})
        verified = {NEIGHBORS[0]: (100.,)*5, GAP: (105., 115., 103., 110., 30.),
                    NEIGHBORS[1]: (120.,)*5}
        filled = repair(original, verified)
        self.assertEqual(filled.loc[GAP].tolist(), list(verified[GAP]))
        pd.testing.assert_frame_equal(filled.drop(index=GAP), original.drop(index=GAP))
        self.assertTrue(original.loc[GAP].isna().all())
        self.assertIsNone(repair(filled, verified))
        self.assertIsNone(repair(original.drop(index=GAP), verified))
        self.assertIsNone(repair(original, dict(verified, **{NEIGHBORS[0]: (90.,)*5})))

    def test_adjacent_close_corrected_only_with_official_boundaries_and_stable_shares(self):
        dates = [OUTER_ANCHORS[0], *DATES, OUTER_ANCHORS[1]]
        frame = pd.DataFrame(
            [[price]*len(FIELDS) for price in (95., 99., None, 110., 115.)],
            columns=FIELDS, index=pd.DatetimeIndex(dates))
        official = dict(zip(dates, [(price,)*len(FIELDS)
                                   for price in (95., 100., 105., 110., 115.)]))
        shares = dict.fromkeys(dates, 1_000_000)
        result = repair(frame, official, shares)
        self.assertEqual(result.at[NEIGHBORS[0], 'Close'], 100.)
        self.assertEqual(result.at[GAP, 'Close'], 105.)
        pd.testing.assert_frame_equal(result.loc[list(OUTER_ANCHORS)],
                                      frame.loc[list(OUTER_ANCHORS)])
        self.assertEqual(frame.at[NEIGHBORS[0], 'Close'], 99.)
        self.assertIsNone(repair(frame, official, dict(shares, **{GAP: 2_000_000})))
        self.assertIsNone(repair(frame, official, dict(shares, **{GAP: None})))
        self.assertIsNone(repair(frame, dict(official, **{OUTER_ANCHORS[0]: (97.,)*5}), shares))

    def test_120_week_position_rejects_missing_prices_and_ignores_partial_week(self):
        weeks = pd.date_range(end='2026-11-06', periods=120, freq='W-FRI')
        history = pd.DataFrame({'Close': 100.}, index=weeks)
        history.loc[pd.Timestamp('2026-11-12')] = 1000.
        self.assertEqual(weekly_position(history, '2026-11-12'),
                         {'asof': '2026-11-06', 'weekly_ma120_gap_pct': 0.})
        self.assertIsNone(weekly_position(history.drop(index=weeks[12]), '2026-11-12'))
        self.assertIsNone(weekly_position(history.iloc[1:], '2026-11-12'))
        history.loc[weeks[12], 'Close'] = 0.
        self.assertIsNone(weekly_position(history, '2026-11-12'))

    def test_calendar_gaps_and_mismatching_anchors_are_rejected(self):
        anchors = {NEIGHBORS[0]: 100., NEIGHBORS[1]: 120.}
        self.assertIsNone(source_frame(payload(), TICKER, list(NEIGHBORS), anchors))
        self.assertIsNone(source_frame(payload(), TICKER, DATES, dict(anchors, **{NEIGHBORS[0]: 90.})))
        self.assertIsNone(source_frame(payload(), '000002.KS', DATES, anchors))

    def test_weekly_rsi_matches_existing_observation_with_completed_closes(self):
        dates = pd.bdate_range(end='2026-11-12', periods=525)
        close = [100 + index % 61 - index // 23 for index in range(len(dates))]
        frame = pd.DataFrame(dict(Open=close, High=close, Low=close, Close=close, Volume=100),
                             index=dates)
        recovered = weekly_rsi(frame, '2026-11-06')
        self.assertIsNotNone(recovered)
        ordinary = observe_completed_weekly(frame)
        self.assertEqual(recovered, {key: ordinary[key] for key in recovered})
        frame.loc['2026-11-09':, 'Close'] = 1000.
        self.assertEqual(weekly_rsi(frame, '2026-11-06'), recovered)
        frame.at[pd.Timestamp('2026-03-02'), 'Close'] = float('nan')
        self.assertIsNone(weekly_rsi(frame, '2026-11-06'))

    def test_weekly_pattern_requires_completed_valid_history_and_matching_local_volume(self):
        dates = pd.bdate_range(end='2026-11-13', periods=600)
        close = [100 + i % 22 for i in range(len(dates))]
        frame = pd.DataFrame(dict(Open=close, High=close, Low=close, Close=close, Volume=100),
                             index=dates)
        anchors = {d: tuple(frame.loc[d, list(FIELDS)]) for d in OUTER_ANCHORS}
        stock = {'price_date': '2026-11-13'}
        self.assertEqual(recover_weekly_pattern(stock, frame, anchors, '2026-11-13'),
                         observe_completed_weekly(frame))
        self.assertIsNone(recover_weekly_pattern(stock, frame, anchors, '2026-11-06'))
        disagree = dict(anchors)
        disagree[OUTER_ANCHORS[0]] = (*anchors[OUTER_ANCHORS[0]][:4], 101)
        self.assertIsNone(recover_weekly_pattern(stock, frame, disagree, '2026-11-13'))
        frame.loc['2026-07-07', ['Open', 'High', 'Low', 'Close']] = 500
        self.assertIsNone(recover_weekly_pattern(stock, frame, anchors, '2026-11-13'))

    def test_weekly_ma120_only_adds_the_missing_same_week_component(self):
        dates = pd.bdate_range(end='2026-11-12', periods=800)
        clean = pd.DataFrame(dict(Open=100., High=100., Low=100., Close=100., Volume=100),
                             index=dates)
        broken = clean.copy()
        broken.iloc[50, broken.columns.get_loc('High')] = 99.
        previous = observe(broken)
        self.assertIsNone(previous['parts']['weekly']['120'])
        expected = observe(clean)
        actual = add_weekly_ma120(previous, broken['Close'], '2026-11-06')
        self.assertEqual(actual, expected)
        self.assertIsNone(previous['parts']['weekly']['120'])
        self.assertIs(add_weekly_ma120(previous, broken['Close'], '2026-11-13'), previous)
        broken.iloc[50, broken.columns.get_loc('Close')] = float('nan')
        self.assertIs(add_weekly_ma120(previous, broken['Close'], '2026-11-06'), previous)
        broken.iloc[50, broken.columns.get_loc('Close')] = 49.
        self.assertIs(add_weekly_ma120(previous, broken['Close'], '2026-11-06'), previous)


if __name__ == '__main__':
    unittest.main()
