import unittest
from unittest.mock import Mock
from flow_collection import FlowCollector


class FlowCollectionTests(unittest.TestCase):
    def test_fallback_failure_not_repeated(self):
        fallback = Mock(side_effect=TimeoutError('table not available'))
        c = FlowCollector(None, fallback)
        value, error = c.collect('000001')
        self.assertIsNone(value)
        self.assertIn('TimeoutError', error)
        fallback.assert_called_once_with('000001')

    def test_timeout_breaker_preserves_missing(self):
        fallback = Mock(side_effect=TimeoutError())
        c = FlowCollector(None, fallback)
        for i in range(3): c.collect(str(i))
        value, error = c.collect('4')
        self.assertIsNone(value)
        self.assertIn('deferred', error)
        self.assertEqual(fallback.call_count, 3)
        self.assertEqual(c.attempted, 3)

    def test_bad_stock_does_not_disable_supplier(self):
        fallback = Mock(side_effect=[ValueError('short history'), {'flow_observations':20}])
        c = FlowCollector(None, fallback, timeout_limit=1)
        c.collect('new')
        self.assertEqual(c.collect('old')[0]['flow_observations'],20)

    def test_budget_and_primary_fallback(self):
        clock = Mock(return_value=0)
        fallback = Mock(return_value={'flow_observations':20})
        c = FlowCollector(Mock(side_effect=ValueError('no data')), fallback,
                          budget_seconds=10, clock=clock)
        self.assertIsNotNone(c.collect('one')[0])
        clock.return_value=10
        self.assertIn('deferred', c.collect('two')[1])
        fallback.assert_called_once()

class NpayParserTests(unittest.TestCase):
    def table(self, date, foreign='+ 21,603 주', inst='- 20,837 주'):
        return ('<table><tr><th>날짜</th><th>외국인</th><th>보유율</th><th>기관</th></tr>'
                f'<tr><td>{date}</td><td>{foreign}</td><td>4.57%</td><td>{inst}</td></tr></table>')

    def test_current_two_digit_year_and_signed_net_shares(self):
        from flow_collection import parse_naver_flow_table
        self.assertEqual(parse_naver_flow_table(self.table('26. 10. 14.')),
                         {'2026-10-14':(-20837,21603)})

    def test_legacy_date_and_missing_share_values(self):
        from flow_collection import parse_naver_flow_table
        self.assertIn('2026-10-14',parse_naver_flow_table(self.table('2026.10.14')))
        self.assertEqual(parse_naver_flow_table(self.table('26.10.14.','-')), {})
        self.assertEqual(parse_naver_flow_table(self.table('26.02.30.')), {})
