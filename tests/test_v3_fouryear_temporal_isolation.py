import copy
import json
from pathlib import Path
import tempfile
import unittest

import v3_dart_four_year_reference as f
from test_v3_dart_four_year_reference import inputs, reports
from test_v3_dart_rolling_book_reference import DAY


class TemporalIsolationTests(unittest.TestCase):
    def test_bad_period_does_not_abort_other_valid_years_or_issuers(self):
        market,catalog,book=inputs()
        bad=reports('005930');bad['reports'][-1]['receipt_id']='20990318001162'
        good=reports('247540')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'derived.json'
            summary=f.write(market,catalog,book,output=path,key='synthetic',limit=2,
                collector=lambda *a,**k:[bad,good])
            saved=json.loads(path.read_text())
        self.assertEqual(summary['temporal_receipts_quarantined_this_run'],1)
        rows={r['code']:r for r in saved['records']}
        self.assertEqual(rows['005930']['observed_fiscal_years'],[2022,2023,2024])
        self.assertFalse(rows['005930']['current_core_financial_research_observed'])
        self.assertTrue(rows['247540']['current_core_financial_research_observed'])
        self.assertEqual(summary['consecutive_healthy_batches'],0)
        self.assertFalse(saved['historical_PIT_source_certified'])

    def test_unverified_noncalendar_period_preserves_previous_valid_year(self):
        prior=f._record('005930',reports('005930')['reports'],DAY)
        new=reports('005930');new['reports'][-1]['receipt_id']='20250618001162'
        new['newly_fetched_annual_years']=4
        clean,review,retained=f.isolate_temporal_receipts([new],{'005930':prior},['005930'],DAY)
        self.assertEqual(len(clean[0]['reports']),4)
        self.assertEqual(clean[0]['reports'][-1]['receipt_id'],prior['reports'][-1]['receipt_id'])
        self.assertEqual(retained,1)
        self.assertEqual(clean[0]['newly_fetched_annual_years'],3)
        self.assertEqual(review['005930'][0]['reason'],'calendar_fiscal_period_unverified')

    def test_bad_currency_identity_or_duplicate_still_blocks_publication(self):
        for kind in ('currency','identity','duplicate'):
            with self.subTest(kind=kind):
                row=reports('005930')
                if kind=='currency':row['reports'][0]['currency']='USD'
                if kind=='identity':row['code']='999999'
                if kind=='duplicate':row['reports'][1]=copy.deepcopy(row['reports'][0])
                with self.assertRaises(f.FourYearEvidenceError):
                    f.isolate_temporal_receipts([row],{},['005930'],DAY)

    def test_strict_record_and_merge_do_not_accept_future_receipts(self):
        market,catalog,book=inputs()
        row=reports('005930');row['reports'][-1]['receipt_id']='20990318001162'
        with self.assertRaises(f.FourYearEvidenceError):
            f.merge(market,catalog,book,[row],limit=1)
