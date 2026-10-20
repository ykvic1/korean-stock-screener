"""Safety tests for DART filing receipt crosschecks."""
import unittest
from datetime import date
from v3_dart_event_filing_research import _classify, _closed_date, EventEvidenceError, _selected_risks

class DARTEventTests(unittest.TestCase):
    def test_classes_are_only_candidate_filing_categories(self):
        self.assertIn('stock_split_or_reverse', _classify('주식분할 결정'))
        self.assertIn('merger', _classify('회사합병 결정'))
        self.assertEqual(_classify('사업보고서'), [])
    def test_historical_date_validation(self):
        self.assertEqual(_closed_date('20161101', date(2015,1,1), date(2017,12,31)), '2016-11-01')
        with self.assertRaises(EventEvidenceError):
            _closed_date('20169999', date(2015,1,1), date(2017,12,31))
    def test_selection_is_bounded_and_non_fabricated(self):
        flags=[{'issuer_market':'KOSPI:005930','quantized_change_bp':-4500},{'issuer_market':'KOSDAQ:035420','quantized_change_bp':3100}]
        self.assertEqual(_selected_risks(flags),['005930','035420'])

if __name__ == '__main__':
    unittest.main()
