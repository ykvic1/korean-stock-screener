import copy
from datetime import date,timedelta
import unittest

from v3_breakout_followthrough import observe,valid


class BreakoutFollowthroughTests(unittest.TestCase):
    def sample(self,prices):
        day=date(2026,11,5)
        sessions=[day-timedelta(days=len(prices)-1-i) for i in range(len(prices))]
        return observe(prices,asof=day.isoformat(),session_days=sessions)

    def test_completed_resistance_break_followthrough_is_distinct_price_only_evidence(self):
        receipt=self.sample([100.]*82+[104.]*18)
        self.assertTrue(valid(receipt,'2026-11-05'))
        self.assertEqual(receipt['points'],1.2)
        self.assertEqual(receipt['confirmed_breakout_age_sessions'],17)
        self.assertFalse(receipt['source_regular_session_verified'])
        self.assertFalse(receipt['investment_score_certified'])

    def test_fresh_break_is_not_mislabeled_as_confirmed_and_false_break_is_zero(self):
        fresh=self.sample([100.]*96+[104.]*4)
        self.assertEqual(fresh['points'],0)
        failed=self.sample([100.]*82+[104.]*3+[97.]+[103.]*14)
        self.assertEqual(failed['points'],0)
        extended=self.sample([100.]*82+[118.]*18)
        self.assertEqual(extended['points'],0)
        self.assertEqual(extended['confirmed_breakout_age_sessions'],17)

    def test_missing_invalid_or_potentially_unadjusted_prices_have_no_observation(self):
        self.assertIsNone(self.sample([100.]*80))
        self.assertIsNone(self.sample([100.]*80+[200.]*20))
        self.assertIsNone(self.sample([100.]*99+[float('nan')]))
        self.assertIsNone(observe([100.]*100,asof='2026-11-05',session_days=None))

    def test_forged_provenance_or_available_weight_is_rejected(self):
        sample=self.sample([100.]*82+[104.]*18)
        for field,value in [('points',2),('source_regular_session_verified',True),
                            ('confirmation_sessions',0),('lookback_sessions',10),
                            ('confirmed_breakout_age_sessions',3)]:
            changed=copy.deepcopy(sample)
            changed[field]=value
            self.assertFalse(valid(changed,'2026-11-05'))


if __name__=='__main__':unittest.main()
