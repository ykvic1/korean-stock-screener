"""No provider calls: strict daily path observations, NEVER new score points."""
from datetime import date,datetime,time,timedelta
import copy
import unittest

from v3_unscored_path_observations import (
    observe, valid_observation, valid_unavailable_reason, classify_unavailable, SCHEMA)

ASOF="2026-10-16"


def sample():
    closes=[100*(1.01**i) for i in range(121)]
    volume=[100.0]*121
    last=date.fromisoformat(ASOF)
    sessions=[last-timedelta(days=120-i) for i in range(121)]
    return closes,volume,sessions


class UnscoredPricePathTests(unittest.TestCase):
    def test_measured_daily_path_never_adds_five_area_points(self):
        close,volume,days=sample()
        out=observe(close,volume,asof=ASOF,session_days=days)
        self.assertEqual(out["schema"],SCHEMA)
        self.assertEqual(out["up_close_days20"],20)
        self.assertEqual(out["positive_nonoverlapping_five_interval_blocks60"],12)
        self.assertEqual(out["complete_post_spike_events_evaluated"],0)
        self.assertEqual(out["post_spike_price_hold_with_volume_easing_events"],0)
        self.assertEqual(out["new_v3_available_points"],0)
        self.assertFalse(out["corporate_actions_adjusted_verified"])
        self.assertTrue(valid_observation(out,ASOF))
        # Pandas DatetimeIndex delivers datetime-like Timestamp objects,
        # not plain date; they must be reduced to the same session date.
        timestamps=[datetime.combine(d,time()) for d in days]
        self.assertEqual(observe(close,volume,asof=ASOF,
                                 session_days=timestamps),out)

    def test_complete_spike_and_five_followup_sessions_are_required(self):
        close,volume,days=sample()
        volume[105]=500
        for i in range(106,111):volume[i]=80
        out=observe(close,volume,asof=ASOF,session_days=days)
        self.assertGreaterEqual(out["complete_post_spike_events_evaluated"],1)
        self.assertGreaterEqual(out["post_spike_price_hold_with_volume_easing_events"],1)
        self.assertEqual(out["new_v3_available_points"],0)
        # Most recent spikes do not have five future sessions; no lookahead.
        volume[105]=100
        volume[119]=700
        last=observe(close,volume,asof=ASOF,session_days=days)
        self.assertEqual(last["complete_post_spike_events_evaluated"],0)

    def test_safe_rejection_reasons_never_promote_missing_price_points(self):
        close,volume,days=sample()
        self.assertTrue(valid_unavailable_reason(
            observe(close,volume,asof=ASOF,session_days=days),None))
        volume[70]=0
        original_close=close[70]
        close[70]=close[69]
        retained=observe(close,volume,asof=ASOF,session_days=days)
        self.assertTrue(valid_observation(retained,ASOF))
        self.assertEqual(retained['up_close_days20'],20)
        self.assertEqual(retained['complete_post_spike_events_evaluated'],0)
        close[70]=original_close
        self.assertIsNone(observe(close,volume,asof=ASOF,session_days=days))
        reason=classify_unavailable(close,volume,asof=ASOF,session_days=days)
        self.assertEqual(reason,"zero_volume_close_changed_without_trade")
        self.assertTrue(valid_unavailable_reason(None,reason))
        self.assertFalse(valid_unavailable_reason(None,None))
        self.assertFalse(valid_unavailable_reason(None,"SECRET_URL"))
        self.assertFalse(valid_unavailable_reason({"real":True},reason))
        volume[70]=100
        close[70]*=2
        self.assertEqual(
            classify_unavailable(close,volume,asof=ASOF,session_days=days),
            "possible_unadjusted_corporate_action")
        close,volume,days=sample()
        self.assertEqual(
            classify_unavailable(close[:90],volume[:90],asof=ASOF,session_days=days[:90]),
            "insufficient_121_sessions")
        days[-1]=days[-2]+timedelta(days=20)
        self.assertEqual(
            classify_unavailable(close,volume,asof=ASOF,session_days=days),
            "irregular_or_stale_session_calendar")

    def test_halt_split_staleness_and_forged_scores_fail_closed(self):
        close,volume,days=sample()
        self.assertIsNone(observe(close,volume,asof=ASOF))
        volume[70]=0
        close[70]=close[69]
        self.assertIsNotNone(observe(close,volume,asof=ASOF,session_days=days))
        volume[70]=100
        close[70]*=2
        self.assertIsNone(observe(close,volume,asof=ASOF,session_days=days))
        close,volume,days=sample()
        days[-1]=days[-2]+timedelta(days=20)
        self.assertIsNone(observe(close,volume,asof=ASOF,session_days=days))
        close,volume,days=sample()
        out=observe(close,volume,asof=ASOF,session_days=days)
        for field,value in (("new_v3_available_points",4),
                            ("investment_score_certified",True),
                            ("raw_ohlcv_exported",True),
                            ("post_spike_price_hold_with_volume_easing_events",9),
                            ("asof","2026-10-15")):
            altered=copy.deepcopy(out);altered[field]=value
            self.assertFalse(valid_observation(altered,ASOF))
        self.assertTrue(valid_observation(None,ASOF))

    def test_historical_halt_never_fabricates_absorption_or_invalid_volume(self):
        close,volume,days=sample()
        volume[105]=500
        for i in range(106,111):volume[i]=80
        good=observe(close,volume,asof=ASOF,session_days=days)
        self.assertGreater(good['post_spike_price_hold_with_volume_easing_events'],0)
        volume[108]=0  # Actual recorded non-trading day with carried close.
        close[108]=close[107]
        result=observe(close,volume,asof=ASOF,session_days=days)
        self.assertIsNotNone(result)
        self.assertEqual(result['post_spike_price_hold_with_volume_easing_events'],0)
        for bad in (-1,float('nan'),float('inf'),True):
            invalid=volume.copy();invalid[70]=bad
            self.assertIsNone(observe(close,invalid,asof=ASOF,session_days=days))
        for i in (0,120):
            invalid=volume.copy();invalid[i]=0
            self.assertIsNone(observe(close,invalid,asof=ASOF,session_days=days))


if __name__=="__main__":unittest.main()
