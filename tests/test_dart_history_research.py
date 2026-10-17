"""Network-free financial history and quarter/YTD source-field contract tests."""
import unittest
from unittest.mock import patch

from official_probe.history import (
    sample_period_financial, sample_history, _reported_amounts,
)

CORP = '00126380'
ASOF = '2026-10-16'
RECEIPT = '20260814000001'


def listing(code='11012', receipt=RECEIPT, received='20260814',
            pages=1, name=None):
    if name is None:
        name = ('반기보고서 (2026.06)' if code == '11012'
                else '분기보고서 (2026.03)')
    return {'status': '000', 'total_page': pages,
            'list': [{'rcept_no': receipt, 'rcept_dt': received,
                      'corp_code': CORP, 'report_nm': name}]}


def row(report='11012', receipt=RECEIPT, **extra):
    result = {
        'corp_code': CORP, 'bsns_year': '2026',
        'reprt_code': report, 'fs_div': 'CFS', 'rcept_no': receipt,
        'sj_div': 'IS', 'account_id': 'ifrs-full_Revenue',
        'currency': 'KRW', 'account_detail': '-',
        'thstrm_amount': '50,000', 'thstrm_add_amount': '110,000',
    }
    result.update(extra)
    return result


class Client:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def _api(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if not self.answers:
            raise AssertionError('unexpected network-like request')
        return self.answers.pop(0)


class HistoryResearchTests(unittest.TestCase):
    def test_half_year_keeps_period_and_cumulative_separate(self):
        client = Client(listing(), {'status': '000', 'list': [
            row(), row(sj_div='CF',
                       account_id='ifrs-full_CashFlowsFromUsedInOperatingActivities',
                       thstrm_amount='', thstrm_add_amount='45,000')]})
        d = sample_period_financial(client, CORP, 2026, '11012', ASOF)
        self.assertEqual(d['status'], 'observed_receipt_matched_revision_unverified')
        self.assertEqual(d['matched_receipt_date'], '20260814')
        self.assertEqual(d['metric_values']['revenue']['thstrm_amount_krw'], 50000)
        self.assertEqual(d['metric_values']['revenue']['thstrm_add_amount_krw'], 110000)
        self.assertIsNone(d['metric_values']['operating_cashflow']['thstrm_amount_krw'])
        self.assertEqual(d['metric_values']['operating_cashflow']['thstrm_add_amount_krw'], 45000)
        self.assertFalse(d['single_quarter_calculated'])
        self.assertFalse(d['revision_history_validated'])
        self.assertFalse(d['asof_join_validated'])
        self.assertFalse(d['investment_data_ready'])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][1]['pblntf_detail_ty'], 'A002')
        self.assertEqual(client.calls[0][1]['end_de'], '20261015')
        self.assertEqual(client.calls[1][1]['reprt_code'], '11012')

    def test_first_quarter_uses_dedicated_period_code(self):
        client = Client(listing(code='11013', receipt='20260514000123', received='20260514'),
                        {'status': '000', 'list': [row(report='11013', receipt='20260514000123')]})
        d = sample_period_financial(client, CORP, 2026, '11013', ASOF)
        self.assertTrue(d['receipt_matched_before_asof'])
        self.assertEqual(client.calls[0][1]['pblntf_detail_ty'], 'A003')

    def test_same_day_filing_blocked(self):
        client = Client(listing(receipt='20261016000123', received='20261016'))
        d = sample_period_financial(client, CORP, 2026, '11012', ASOF)
        self.assertEqual(d['status'], 'no_matching_period_filing')
        self.assertEqual(len(client.calls), 1)

    def test_after_cutoff_revision_blocked(self):
        client = Client(listing(), {'status': '000', 'list': [
            row(receipt='20261018000123')]})
        d = sample_period_financial(client, CORP, 2026, '11012', ASOF)
        self.assertEqual(d['status'], 'statement_receipt_not_visible_asof')
        self.assertIsNone(d['metric_values'])

    def test_incomplete_search_blocks_financial_request(self):
        client = Client(listing(pages=2))
        d = sample_period_financial(client, CORP, 2026, '11012', ASOF)
        self.assertEqual(d['status'], 'filing_search_incomplete')
        self.assertEqual(len(client.calls), 1)

    def test_wrong_report_period_is_blocked(self):
        client = Client(listing(name='반기보고서 (2025.06)'))
        d = sample_period_financial(client, CORP, 2026, '11012', ASOF)
        self.assertEqual(d['status'], 'no_matching_period_filing')

    def test_wrong_corp_and_mixed_receipts_fail(self):
        for rows in ([row(corp_code='00000000')],
                     [row(), row(receipt='20260814000002')]):
            with self.subTest(rows=rows):
                d = sample_period_financial(
                    Client(listing(), {'status': '000', 'list': rows}),
                    CORP, 2026, '11012', ASOF)
                self.assertEqual(d['status'], 'statement_schema_or_receipt_inconsistent')
                self.assertIsNone(d['metric_values'])

    def test_ambiguous_currency_and_detail_are_not_inferred(self):
        rows = [row(thstrm_amount='100'), row(thstrm_amount='200'),
                row(sj_div='BS',account_id='ifrs-full_Assets', currency='USD'),
                row(sj_div='CF', account_id='ifrs-full_CashFlowsFromUsedInOperatingActivities',
                    account_detail='segment')]
        client = Client(listing(), {'status': '000', 'list': rows})
        d = sample_period_financial(client, CORP, 2026, '11012', ASOF)
        self.assertIsNone(d['metric_values']['revenue']['thstrm_amount_krw'])
        self.assertIsNone(d['metric_values']['assets']['thstrm_amount_krw'])
        self.assertIsNone(d['metric_values']['operating_cashflow']['thstrm_add_amount_krw'])

    def test_period_not_completed_does_not_call_api(self):
        c = Client()
        d = sample_period_financial(c, CORP, 2026, '11012', '2026-05-20')
        self.assertEqual(d['status'], 'report_period_not_ended')
        self.assertFalse(c.calls)

    def test_limit_and_unknown_report_code(self):
        for code in ('11011', '00000'):
            with self.assertRaises(ValueError):
                sample_period_financial(Client(), CORP, 2026, code, ASOF)
        with self.assertRaises(ValueError):
            sample_history(Client(), CORP, ASOF, years=4)

    def test_dry_run_history_orchestration_without_network(self):
        def annual(_, corp, year, asof, fs_div='CFS'):
            return {'status': 'observed_receipt_matched_revision_unverified',
                    'business_year': year, 'fs_div': fs_div}
        def period(_, corp, year, code, asof, fs_div='CFS'):
            return {'status': 'observed_receipt_matched_revision_unverified',
                    'report_code': code, 'fs_div': fs_div}
        with patch('official_probe.history.sample_annual_financial', side_effect=annual), \
             patch('official_probe.history.sample_period_financial', side_effect=period):
            out = sample_history(Client(), CORP, ASOF, years=3)
        self.assertEqual([x['business_year'] for x in out['annual']], [2025, 2024, 2023])
        self.assertEqual([x['report_code'] for x in out['current_year_periods']], ['11013','11012'])
        self.assertTrue(out['annual_scope_consistent'])
        self.assertFalse(out['revision_history_validated'])
        self.assertFalse(out['investment_data_ready'])
        self.assertFalse(out['flow_quarter_values_derived'])

    def test_mixed_cfs_ofs_history_marks_scope_incomparable(self):
        def annual(_, corp, year, asof, fs_div='CFS'):
            if year == 2024 and fs_div == 'CFS':
                return {'status': 'annual_statement_not_available', 'fs_div':'CFS'}
            return {'status':'observed_receipt_matched_revision_unverified',
                    'fs_div':fs_div}
        def period(_, corp, year, code, asof, fs_div='CFS'):
            return {'status':'observed_receipt_matched_revision_unverified',
                    'fs_div':fs_div}
        with patch('official_probe.history.sample_annual_financial', side_effect=annual), \
             patch('official_probe.history.sample_period_financial', side_effect=period):
            out = sample_history(Client(), CORP, ASOF, years=3)
        self.assertFalse(out['annual_scope_consistent'])
        self.assertEqual(out['accounting_bases_seen'], ['CFS','OFS'])
        self.assertTrue(out['annual'][1]['used_OFS_fallback'])

    def test_cli_history_refuses_dry_run_and_more_than_one_stock(self):
        import json
        import tempfile
        from pathlib import Path
        from contextlib import redirect_stderr
        from io import StringIO
        from official_probe.__main__ import run

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'data.json'
            source.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code': '252990', 'market': 'KOSDAQ'},
                {'code': '491000', 'market': 'KOSDAQ'},
            ]}), encoding='utf-8')
            args = ['--asof', ASOF, '--provider', 'dart',
                    '--source', str(source), '--history-sample']
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                run(args + ['--symbols', '252990'])
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                run(args + ['--live', '--symbols', '252990,491000'])

    def test_cli_history_live_one_company_no_key_does_not_network(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from official_probe.__main__ import run
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'data.json'
            output = Path(tmp) / 'probe.json'
            source.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code': '252990', 'market': 'KOSDAQ'}
            ]}), encoding='utf-8')
            with patch.dict(os.environ, {'DART_API_KEY': ''}):
                result = run(['--asof', ASOF, '--provider', 'dart', '--live',
                              '--history-sample', '--symbols', '252990',
                              '--source', str(source), '--output', str(output)])
            self.assertEqual(result['dart']['status'], 'key_required')
            self.assertFalse(result['investment_data_ready'])
            self.assertTrue(output.exists())

if __name__ == '__main__':
    unittest.main()
