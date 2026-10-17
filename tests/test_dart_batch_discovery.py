"""Offline fail-closed tests for bounded OpenDART annual filing discovery."""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from official_probe.batch_discovery import discover_annual_receipt
from official_probe.__main__ import run

ASOF = '2026-10-16'
CORP = '01199727'
RECEIPT = '20260316001120'


def filing(receipt=RECEIPT, report='사업보고서 (2025.12)', corp=CORP):
    return {'rcept_no': receipt, 'rcept_dt': receipt[:8],
            'report_nm': report, 'corp_code': corp}


class FakeFilingClient:
    def __init__(self, items=None, *, pages=1, status='000'):
        self.items = [filing()] if items is None else items
        self.pages = pages
        self.status = status
        self.calls = []

    def _api(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return {'status': self.status, 'total_page': self.pages, 'list': self.items}


class AnnualDiscoveryTests(unittest.TestCase):
    def test_single_original_is_only_an_unverified_receipt_candidate(self):
        c = FakeFilingClient()
        result = discover_annual_receipt(c, CORP, 2025, ASOF)
        self.assertEqual(result['receipt_candidate'], RECEIPT)
        self.assertEqual(result['status'], 'single_original_annual_receipt_candidate_unverified')
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(c.calls[0][0], 'list.json')
        self.assertEqual(c.calls[0][1]['pblntf_detail_ty'], 'A001')
        self.assertEqual(c.calls[0][1]['last_reprt_at'], 'N')
        self.assertEqual(c.calls[0][1]['end_de'], '20261015')
        self.assertTrue(result['scope_choice_required'])
        for key in ('financial_api_receipt_matches', 'xbrl_accounting_scope_verified',
                    'historical_numeric_snapshot_verified', 'asof_join_validated',
                    'revision_history_validated', 'investment_data_ready',
                    'confirmed_scoring_allowed'):
            self.assertFalse(result[key])

    def test_amendment_and_two_receipts_not_promoted(self):
        for filings in (
            [filing(), filing('20260715000001', '[정정]사업보고서 (2025.12)')],
            [filing(RECEIPT, '[정정]사업보고서 (2025.12)')],
            [filing(), filing('20260715000001')],
        ):
            with self.subTest(filings=filings):
                r = discover_annual_receipt(FakeFilingClient(filings), CORP, 2025, ASOF)
                self.assertEqual(r['status'], 'multiple_or_amended_reports_require_manual_review')
                self.assertIsNone(r['receipt_candidate'])
                self.assertFalse(r['investment_data_ready'])

    def test_incomplete_invalid_or_missing_listing_fails_closed(self):
        cases = [
            (FakeFilingClient(pages=2), 'filing_listing_incomplete'),
            (FakeFilingClient([filing(), filing()]), 'filing_row_identity_invalid_or_duplicate'),
            (FakeFilingClient([filing('20261016000001')]), 'filing_row_identity_invalid_or_duplicate'),
            (FakeFilingClient([filing('20260230000001')]), 'filing_row_date_invalid'),
            (FakeFilingClient([filing(corp='99999999')]), 'filing_row_identity_invalid_or_duplicate'),
            (FakeFilingClient([filing(report='반기보고서 (2025.06)')]), 'no_2025_annual_report_match'),
            (FakeFilingClient(status='013'), 'no_report_in_window'),
        ]
        for c, expected in cases:
            with self.subTest(expected=expected):
                r = discover_annual_receipt(c, CORP, 2025, ASOF)
                self.assertEqual(r['status'], expected)
                self.assertIsNone(r['receipt_candidate'])

    def test_cli_discovery_requires_explicit_two_to_five_no_other_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'data.json'
            path.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code': '252990', 'market': 'KOSDAQ'},
                {'code': '280360', 'market': 'KOSPI'}]}), encoding='utf-8')
            base = ['--asof', ASOF, '--provider', 'dart', '--symbols',
                    '252990,280360', '--xbrl-discover-receipts',
                    '--source', str(path)]
            for args in [
                base,  # live missing
                base+['--live', '--finance-sample'],
                base+['--live', '--xbrl-batch-cases',
                      '252990:20260316001120:OFS,280360:20260316001121:OFS'],
                ['--asof', ASOF, '--provider', 'dart', '--live',
                 '--symbols', '252990', '--xbrl-discover-receipts',
                 '--source', str(path)],
            ]:
                with self.subTest(args=args), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        with patch('official_probe.__main__.DARTClient', side_effect=AssertionError('network attempted')):
                            run(args)

    def test_live_discovery_five_companies_one_list_each_no_numbers(self):
        stocks = ['252990', '280360', '018880', '014680', '064290']
        mapping = {s: '01234'+str(i).zfill(3) for i,s in enumerate(stocks)}
        class FakeDART:
            def __init__(self, key, session=None):
                self.calls = []
            def corp_mapping(self):
                return mapping
            def _api(self, endpoint, **kw):
                self.calls.append((endpoint, kw))
                return {'status': '000', 'total_page': 1, 'list': [
                    filing(receipt=RECEIPT, corp=kw['corp_code'])]}
            def recent_filings(self, *args):
                raise AssertionError('discovery must not request unrelated listings')
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'data.json'
            output=Path(tmp)/'research.json'
            src.write_text(json.dumps({'date':ASOF,'stocks':[
                {'code':code,'market':('KOSDAQ' if i%2 == 0 else 'KOSPI')}
                for i,code in enumerate(stocks)]}),encoding='utf-8')
            with patch.dict(os.environ,{'DART_API_KEY':'OFFLINE_KEY'}):
                with patch('official_probe.__main__.DARTClient', FakeDART):
                    with redirect_stdout(StringIO()):
                        result=run(['--asof',ASOF,'--provider','dart','--live',
                            '--xbrl-discover-receipts','--symbols',','.join(stocks),
                            '--source',str(src),'--output',str(output)])
            companies=result['dart']['companies_sampled']
            self.assertEqual(len(companies),5)
            self.assertTrue(all(c['annual_receipt_discovery']['receipt_candidate']==RECEIPT for c in companies))
            self.assertTrue(result['dart']['annual_receipt_discovery_pilot']['scope_choice_required'])
            self.assertFalse(result['dart']['annual_receipt_discovery_pilot']['investment_data_ready'])
            self.assertFalse(result['confirmed_scoring_allowed'])
            self.assertTrue(output.exists())


if __name__ == '__main__':
    unittest.main()
