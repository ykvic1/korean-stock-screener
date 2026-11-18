"""Exercise the frozen-rank controls without dropping unresolved candidates."""
import unittest

from v3_forward_rank_comparison import weighted_comparison


class ForwardRankComparisonTests(unittest.TestCase):
    def setUp(self):
        self.members = {
            str(rank): [str(rank), market, 70, rank]
            for rank, market in ((1, 'KOSPI'), (2, 'KOSPI'), (3, 'KOSDAQ'),
                                 (21, 'KOSPI'), (22, 'KOSDAQ'), (100, 'KOSDAQ'),
                                 (101, 'KOSPI'), (103, 'KOSDAQ'))
        }
        self.openings = dict.fromkeys(self.members, ('official_open_observed', 100))
        self.returns = {'1': 20, '3': -10, '21': 5, '22': -20, '100': 40,
                        '101': -95, '103': -99}

    def test_disjoint_controls_use_the_weight_of_the_frozen_candidates(self):
        report = weighted_comparison(self.members, self.openings, self.returns, 20, 100)
        self.assertEqual((report['reference_min_rank'], report['reference_max_rank']), (21, 100))
        self.assertEqual(report['observed_mean_return_spread_pct_points'], 3.3333)
        self.assertEqual(report['loss_rate_spread_bounds_pct_points'], [16.6666, 50])
        self.assertEqual(report['markets']['KOSPI']['candidate']['selected_count'], 2)
        self.assertEqual(report['markets']['KOSPI']['candidate']['valued_count'], 1)
        self.assertEqual(report['markets']['KOSDAQ']['reference']['selected_count'], 2)
        self.assertEqual(report['markets']['KOSDAQ']['reference']['negative_return_count'], 1)
        top100 = weighted_comparison(self.members, self.openings, self.returns, 100, None)
        self.assertEqual(top100['reference_min_rank'], 101)
        self.assertEqual(top100['markets']['KOSPI']['reference']['negative_return_count'], 1)

    def test_no_control_opening_in_one_market_prevents_a_cross_market_comparison(self):
        self.openings['21'] = ('not_traded', None)
        del self.returns['21']
        report = weighted_comparison(self.members, self.openings, self.returns, 20, 100)
        self.assertIsNone(report['observed_mean_return_spread_pct_points'])
        self.assertIsNone(report['loss_rate_spread_bounds_pct_points'])
        self.assertEqual(report['markets']['KOSPI']['reference']['confirmed_opening_count'], 0)
        self.assertEqual(report['markets']['KOSDAQ']['reference']['selected_count'], 2)
