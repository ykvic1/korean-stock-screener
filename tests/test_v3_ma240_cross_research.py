"""Recent bullish MA20/MA60 intersection versus SAME-TIME MA240, not close."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from v3_ma240_cross_research import (
    observe, valid_observation, classify_unavailable,
    valid_unavailable_reason, SCHEMA)


ASOF = "2026-11-03"


def sessions(n=260):
    end = date.fromisoformat(ASOF)
    days = []
    cursor = end
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def sample(closes):
    return observe(closes, [1000]*len(closes),
                   asof=ASOF,session_days=sessions(len(closes)))


class RecentMa240CrossTests(unittest.TestCase):
    def test_historical_zero_volume_does_not_hide_observed_price_ma_cross(self):
        rows = [100.0]*220 + [98.0]*20 + [130.0]*20
        baseline = sample(rows)
        volume = [1000]*260
        for i in (5, 100, 239):
            volume[i] = 0
        result = observe(rows, volume, asof=ASOF, session_days=sessions())
        self.assertEqual(result, baseline)
        self.assertTrue(result['qualified'])
        self.assertTrue(valid_observation(result, ASOF))
        for bad in (-1, float('nan'), float('inf'), True):
            broken = volume.copy()
            broken[100] = bad
            with self.subTest(bad=bad):
                self.assertIsNone(observe(rows, broken, asof=ASOF, session_days=sessions()))
        volume[-1] = 0
        self.assertIsNone(observe(rows, volume, asof=ASOF, session_days=sessions()))

    def test_qualifying_cross_is_at_intersection_not_current_close(self):
        # Price ends at 130, far above MA240, yet the actual crossing
        # occurred close to the 240-day line after a 20-day downtrend.
        rows = [100.0]*220 + [98.0]*20 + [130.0]*20
        result = sample(rows)
        self.assertIsNotNone(result)
        self.assertEqual(result["schema"],SCHEMA)
        self.assertTrue(result["qualified"])
        self.assertLessEqual(abs(result["qualifying_cross_point_vs_ma240_pct"]),12)
        self.assertGreater(100*(rows[-1]/(sum(rows[-240:])/240)-1),12)
        self.assertTrue(valid_observation(result,ASOF))
        self.assertFalse(result["closing_price_band_test_used"])
        self.assertFalse(result["source_regular_session_verified"])

    def test_observed_no_cross_is_real_zero_not_unavailable(self):
        result = sample([100.0]*260)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["cross_event_count"],0)
        self.assertTrue(valid_observation(result,ASOF))

    def test_cross_outside_ma240_band_does_not_get_six(self):
        rows = [60.0]*200 + [90.0]*20 + [60.0]*20 + [85.0]*20
        result = sample(rows)
        self.assertIsNotNone(result)
        self.assertGreater(result["cross_event_count"],0)
        self.assertFalse(result["qualified"])
        self.assertGreater(abs(result["last_cross_point_vs_ma240_pct"]),12)
        self.assertTrue(valid_observation(result,ASOF))

    def test_missing_bars_and_bad_calendar_are_unavailable(self):
        self.assertIsNone(sample([100.0]*259))
        rows=[100.0]*220+[98.0]*20+[130.0]*20
        self.assertIsNone(observe(rows,[1000]*260,asof=ASOF,
                                  session_days=list(reversed(sessions()))))
        self.assertIsNone(observe(rows,[1000]*260,asof=ASOF,
                                  session_days=sessions(259)))
        self.assertIsNone(observe(rows,[1000]*259+[0],asof=ASOF,
                                  session_days=sessions()))
        self.assertIsNone(observe(rows,[1000]*260,asof="2026-11-04",
                                  session_days=sessions()))
        self.assertIsNone(sample([100.0]*259+[200.0]))

    def test_stale_or_forged_receipt_cannot_gain_points(self):
        good=sample([100.0]*220+[98.0]*20+[130.0]*20)
        for updates in (
            {"qualified":True,"qualifying_cross_point_vs_ma240_pct":15},
            {"source_regular_session_verified":True},
            {"closing_price_band_test_used":True},
            {"certified_investment_points":6},
            {"asof":"2026-11-04"},
            {"cross_event_count":21},
        ):
            forged={**deepcopy(good),**updates}
            self.assertFalse(valid_observation(forged,ASOF))

    def test_unavailable_reasons_use_real_rejected_inputs_only(self):
        close = [100.0]*260
        volume = [1000]*260
        dates = sessions()
        cases = (
            (close[:259], volume[:259], sessions(259),
             "insufficient_260_sessions"),
            (close, volume[:259], dates,
             "missing_or_mismatched_session_index"),
            (close, volume, list(reversed(dates)),
             "irregular_or_stale_session_calendar"),
            (close[:259]+[0], volume, dates,
             "nonpositive_or_invalid_close"),
            (close, volume[:259]+[0], dates,
             "nonpositive_or_invalid_volume"),
            (close[:259]+[200], volume, dates,
             "possible_unadjusted_corporate_action"),
        )
        for prices, vols, days, expected in cases:
            with self.subTest(expected=expected):
                self.assertIsNone(observe(prices, vols, asof=ASOF,
                                          session_days=days))
                reason = classify_unavailable(
                    prices, vols, asof=ASOF, session_days=days)
                self.assertEqual(reason, expected)
                self.assertTrue(valid_unavailable_reason(None, reason))
        valid = sample(close)
        self.assertIsNotNone(valid)
        self.assertTrue(valid_unavailable_reason(valid, None))
        self.assertFalse(valid_unavailable_reason(valid,
                         "insufficient_260_sessions"))
        self.assertFalse(valid_unavailable_reason(None,
                         "untrusted_claim_of_six_points"))


if __name__=="__main__":
    unittest.main()
