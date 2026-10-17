"""Offline contract tests: never use a real API key or network calls."""
import unittest

from official_probe.clients import ProbeError
from official_probe.financial import sample_annual_financial, _krw


CORP = '00126380'
RECEIPT = '20260331001234'
ASOF = '2026-10-16'


def annual_listing(receipt=RECEIPT, day='20260331', pages=1,
                   report='사업보고서 (2025.12)', corp=CORP):
    return {'status': '000', 'total_page': pages, 'list': [
        {'rcept_no': receipt, 'rcept_dt': day, 'report_nm': report,
         'corp_code': corp}]}


def row(account, value, receipt=RECEIPT, **extra):
    result = {'rcept_no': receipt, 'corp_code': CORP, 'bsns_year': '2025',
              'reprt_code': '11011', 'fs_div': 'CFS', 'sj_div': 'IS',
              'account_id': account, 'thstrm_amount': value,
              'account_detail': '-', 'currency': 'KRW'}
    result.update(extra)
    return result


class FakeDART:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def _api(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.answers.pop(0)


class AnnualFinancialSampleTests(unittest.TestCase):
    def test_receipt_matched_amounts_but_pit_still_unverified(self):
        rows = [row('ifrs-full_Revenue', '1,000,000,000'),
                row('dart_OperatingIncomeLoss', '120,000,000'),
                row('ifrs-full_CashFlowsFromUsedInOperatingActivities',
                    '150,000,000', sj_div='CF'),
                row('ifrs-full_Assets', '9,000,000,000', sj_div='BS')]
        client = FakeDART(annual_listing(), {'status': '000', 'list': rows})
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'observed_receipt_matched_revision_unverified')
        self.assertEqual(out['matched_receipt_date'], '20260331')
        self.assertTrue(out['receipt_matched_before_asof'])
        self.assertFalse(out['asof_join_validated'])
        self.assertFalse(out['revision_history_validated'])
        self.assertFalse(out['investment_data_ready'])
        self.assertFalse(out['financial_metrics_verified'])
        self.assertEqual(out['metric_values']['revenue']['amount_krw'], 1000000000)
        self.assertEqual(out['metric_values']['operating_income']['amount_krw'], 120000000)
        self.assertEqual(out['metric_values']['operating_cashflow']['amount_krw'], 150000000)
        self.assertEqual(out['metric_values']['assets']['amount_krw'], 9000000000)
        self.assertIsNone(out['metric_values']['liabilities']['amount_krw'])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][0], 'list.json')
        self.assertEqual(client.calls[0][1]['pblntf_detail_ty'], 'A001')
        self.assertEqual(client.calls[0][1]['last_reprt_at'], 'N')
        self.assertEqual(client.calls[0][1]['end_de'], '20261015')
        self.assertEqual(client.calls[1][0], 'fnlttSinglAcntAll.json')

    def test_same_day_filing_never_proves_availability(self):
        client = FakeDART(annual_listing('20261016000123', '20261016'))
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'no_matching_annual_filing_receipt')
        self.assertEqual(len(client.calls), 1)

    def test_after_asof_amendment_cannot_supply_historical_values(self):
        client = FakeDART(annual_listing(), {
            'status': '000',
            'list': [row('ifrs-full_Revenue', '2,000', receipt='20261018000001')]})
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'annual_statement_receipt_not_matched_asof')
        self.assertIsNone(out['metric_values'])
        self.assertFalse(out['receipt_matched_before_asof'])

    def test_ambiguous_and_foreign_currency_must_be_null(self):
        rows = [row('ifrs-full_Revenue', '1,000'),
                row('ifrs-full_Revenue', '2,000'),
                row('dart_OperatingIncomeLoss', '77', currency='USD'),
                row('ifrs-full_CashFlowsFromUsedInOperatingActivities',
                    '25', sj_div='CF', account_detail='some segment')]
        client = FakeDART(annual_listing(), {'status': '000', 'list': rows})
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertIsNone(out['metric_values']['revenue']['amount_krw'])
        self.assertIsNone(out['metric_values']['operating_income']['amount_krw'])
        self.assertIsNone(out['metric_values']['operating_cashflow']['amount_krw'])

    def test_pagination_never_infers_complete_revision_history(self):
        client = FakeDART(annual_listing(pages='2'))
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'annual_filing_search_incomplete')
        self.assertEqual(len(client.calls), 1)

    def test_report_name_must_match_business_year(self):
        client = FakeDART(annual_listing(report='사업보고서 (2024.12)'))
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'no_matching_annual_filing_receipt')

    def test_statement_wrong_corp_or_mixed_receipts_is_blocked(self):
        for entry in [
            row('ifrs-full_Revenue', '1,000', corp_code='00000000'),
            row('ifrs-full_Revenue', '1,000', reprt_code='11013'),
            row('ifrs-full_Revenue', '1,000', bsns_year='2024'),
        ]:
            with self.subTest(entry=entry):
                client = FakeDART(annual_listing(), {'status': '000', 'list': [entry]})
                out = sample_annual_financial(client, CORP, 2025, ASOF)
                self.assertEqual(out['status'], 'annual_statement_schema_or_receipt_inconsistent')
                self.assertIsNone(out['metric_values'])

    def test_missing_or_early_reports_do_not_call_financial_endpoint(self):
        early = FakeDART()
        out = sample_annual_financial(early, CORP, 2025, '2025-12-31')
        self.assertEqual(out['status'], 'annual_report_not_yet_eligible')
        self.assertFalse(early.calls)
        no_filing = FakeDART({'status': '013'})
        out = sample_annual_financial(no_filing, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'no_annual_filing_before_asof')
        self.assertEqual(len(no_filing.calls), 1)

    def test_no_statement_rows_returns_no_data(self):
        client = FakeDART(annual_listing(), {'status': '013'})
        out = sample_annual_financial(client, CORP, 2025, ASOF)
        self.assertEqual(out['status'], 'annual_statement_not_available')
        self.assertIsNone(out['metric_values'])

    def test_amount_rejects_noise(self):
        for value in ('-', '', '1.2', '1,00', '10만원', None, 'nan'):
            with self.subTest(value=value):
                self.assertIsNone(_krw(value))
        self.assertEqual(_krw('-1,234'), -1234)


if __name__ == '__main__':
    unittest.main()
