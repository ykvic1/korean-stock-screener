import unittest
from mobile_smoke_contract import check_flow_filter


class MobileFlowSmokeContractTests(unittest.TestCase):
    def test_zero_flows_is_explicitly_unverified_not_successful_collection(self):
        data = {'flow_confirmed_count': 0, 'stocks': [
            {'code': '000001', 'flows_confirmed': False},
            {'code': '000002', 'flows_confirmed': False},
        ]}
        status = check_flow_filter(data, 0)
        self.assertEqual(status['collection_status'], 'NO_CONFIRMED_FLOW_DATA')
        self.assertFalse(status['investment_data_ready'])
        self.assertEqual(status['rendered'], 0)

    def test_some_flows_requires_exact_filter_count(self):
        data = {'flow_confirmed_count': 1, 'stocks': [
            {'code': '000001', 'flows_confirmed': True},
            {'code': '000002', 'flows_confirmed': False},
        ]}
        status = check_flow_filter(data, 1)
        self.assertEqual(status['collection_status'], 'SOME_FLOW_DATA_REPORTED')
        self.assertFalse(status['investment_data_ready'])

    def test_rejects_flag_mismatch_even_when_filter_matches_summary(self):
        data = {'flow_confirmed_count': 0, 'stocks': [
            {'code': '000001', 'flows_confirmed': True},
        ]}
        with self.assertRaisesRegex(AssertionError, 'differs'):
            check_flow_filter(data, 0)

    def test_rejects_wrong_rendered_filter_count(self):
        data = {'flow_confirmed_count': 1, 'stocks': [
            {'code': '000001', 'flows_confirmed': True},
        ]}
        with self.assertRaisesRegex(AssertionError, 'shows 0'):
            check_flow_filter(data, 0)

    def test_rejects_missing_negative_and_noninteger_counts(self):
        for bad in (-1, 3, None, True, '0'):
            with self.subTest(bad=bad), self.assertRaises(AssertionError):
                check_flow_filter({'flow_confirmed_count': bad, 'stocks': [
                    {'code': '000001', 'flows_confirmed': False}
                ]}, 0)

    def test_empty_universe_does_not_imply_data_quality_success(self):
        self.assertFalse(check_flow_filter({'flow_confirmed_count': 0, 'stocks': []}, 0)['investment_data_ready'])


if __name__ == '__main__':
    unittest.main()
