"""Recent bullish MA20/MA60 intersection versus SAME-TIME MA240, not close."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from v3_ma240_cross_research import observe, valid_observation, SCHEMA


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


if __name__=="__main__":
    unittest.main()
