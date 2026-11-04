import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from v3_dart_reported_eps import (amount, metrics, parse, plan, collect, snapshot,
                                  EPSError, SCHEMA, main, safe_failure_label)

DAY = '2026-11-03'
BASE = {'code': '005930', 'name': 'Synthetic', 'market': 'KOSPI', 'fiscal_year': 2025,
        'fs_div': 'CFS', 'annual_receipt_id': '20260317000001'}


def payload():
    return {'status': '000', 'list': [{
        'corp_code': '00123456', 'bsns_year': '2025', 'reprt_code': '11011',
        'fs_div': 'CFS', 'sj_div': 'IS', 'currency': 'KRW', 'rcept_no': BASE['annual_receipt_id'],
        'account_id': 'ifrs-full_BasicEarningsLossPerShare', 'account_detail': '-',
        'thstrm_amount': '144.50', 'frmtrm_amount': '120', 'bfefrmtrm_amount': '100'}]}


def inputs():
    return ({'date': DAY, 'listed_total': 1, 'rows': [
        {'code': BASE['code'], 'name': BASE['name'], 'market': BASE['market']}]},
        {'asof': DAY, 'raw_dart_account_rows_published': False,
         'historical_point_in_time_verified': False, 'records': [
             {'code': BASE['code'], 'status': 'observed', 'annual': {
                 'year': 2025, 'fs_div': 'CFS', 'currency': 'KRW', 'receipt_id': BASE['annual_receipt_id']}}]})


class EPSTests(unittest.TestCase):
    def test_source_diagnostics_are_allowlisted_not_raw_provider_strings(self):
        self.assertEqual(safe_failure_label(EPSError('official_eps_transport_unavailable')),
                         'blocked: official_eps_transport_unavailable')
        self.assertEqual(safe_failure_label(EPSError('partial_eps_source_stopped_after_checkpoint')),
                         'blocked: partial_eps_source_stopped_after_checkpoint')
        for error in (EPSError('private_key=not-for-logs'), ValueError('provider response')):
            self.assertEqual(safe_failure_label(error),
                             'blocked: reported_eps_source_not_verified')

    def test_partial_checkpoint_stops_outer_continuous_collection_on_source_failure(self):
        catalog, book = inputs()
        catalog['rows'].append({'code': '000001', 'name': 'Second', 'market': 'KOSDAQ'})
        catalog['listed_total'] = 2
        book['records'].append({**copy.deepcopy(book['records'][0]), 'code': '000001'})

        class Reply:
            def raise_for_status(self): pass
            def json(self): return payload()

        class Session:
            calls = 0
            def get(self, url, **kwargs):
                self.calls += 1
                if kwargs['params']['corp_code'] == '00000001':
                    raise OSError('upstream unavailable')
                return Reply()

        session = Session()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'v22-market-catalog.json').write_text(json.dumps(catalog))
            (root/'v3-dart-book-reference.json').write_text(json.dumps(book))
            with (patch('v3_dart_reported_eps.verified_company_mapping_cache',
                        return_value={BASE['code']: '00123456', '000001': '00000001'}),
                  patch('v3_dart_reported_eps._read_company_cache_from_root', return_value=None),
                  patch.dict('sys.modules', {'requests': SimpleNamespace(Session=lambda: session)}),
                  patch.dict(os.environ, {'DART_API_KEY': 'a'*40}),
                  self.assertRaisesRegex(EPSError, 'partial_eps_source_stopped_after_checkpoint')):
                main(['--docs', folder, '--mode', 'live'])
            saved = json.loads((root/'v3-dart-reported-eps.json').read_text())
            self.assertEqual(saved['attempted_issuers'], 1)
            self.assertEqual(saved['pending_issuers'], 1)
            self.assertEqual(session.calls, 2)

    def test_unusable_issuer_does_not_block_other_issuers_or_become_zero_eps(self):
        catalog, book = inputs()
        catalog['rows'].append({'code': '000001', 'name': 'Second', 'market': 'KOSDAQ'})
        catalog['listed_total'] = 2
        book['records'].append({**copy.deepcopy(book['records'][0]), 'code': '000001'})
        class Reply:
            def __init__(self, corp): self.corp=corp
            def raise_for_status(self): pass
            def json(self):
                p=payload()
                p['list'][0]['corp_code']=self.corp
                if self.corp=='00123456':
                    p['list'].append({**p['list'][0], 'thstrm_amount': '999'})
                return p
        class Session:
            def get(self, url, **kwargs): return Reply(kwargs['params']['corp_code'])
        with patch('v3_dart_reported_eps.verified_company_mapping_cache', return_value={BASE['code']: '00123456', '000001': '00000001'}):
            report,calls=collect(catalog,book,None,key='a'*40,session=Session())
        self.assertEqual(calls,2)
        self.assertEqual(report['pending_issuers'],0)
        self.assertEqual(report['unusable_source_issuers'],1)
        self.assertEqual(report['records'][BASE['code']]['reported_basic_eps_2023_2025'],[None]*3)
        self.assertTrue(report['records']['000001']['metrics']['complete_three_years'])
        self.assertEqual(plan(catalog,book,report)[-1],[])

    def test_previous_day_annual_book_keeps_receipt_identity_but_future_book_is_rejected(self):
        catalog, book = inputs()
        book['asof'] = '2026-11-02'
        self.assertEqual(plan(catalog, book)[-1], [BASE['code']])
        book['asof'] = '2026-11-04'
        with self.assertRaises(EPSError):
            plan(catalog, book)

    def test_live_workflow_is_bounded_and_offline_checks_never_use_secrets(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root/'.github/workflows/v3-dart-reported-eps.yml').read_text()
        offline, live = workflow.split('  reported-eps:', 1)
        self.assertNotIn('secrets.', offline)
        self.assertNotIn('concurrency:', offline)
        self.assertIn('needs: offline-safety', live)
        self.assertIn('--mode live --limit 100', live)
        self.assertIn('--mode refresh', live)
        self.assertIn('--mode verify', live)
        self.assertIn('git add docs/v3-dart-reported-eps.json', live)
        self.assertNotIn('git add .', live)
        self.assertNotIn('upload-artifact', workflow)
        self.assertIn("out.write('ready='+('true' if ready else 'false')+'\\n')", live)
        self.assertIn('source_interrupted=1', live)
        self.assertIn('eps_final', live)
        self.assertIn('reported_eps_no_verified_checkpoint_after_source_failure', live)
        self.assertIn('if [[ "$eps_final" -le "$eps_start" ]]', live)
        self.assertIn('reported_eps_source_interrupted_after_saved_checkpoint', live)

    def test_exact_annual_comparatives_keep_decimal_eps_and_recompute_growth(self):
        r = parse(payload(), corp='00123456', base=BASE, on_day=DAY)
        self.assertEqual(r['reported_basic_eps_2023_2025'], [100, 120, 144.5])
        self.assertTrue(r['metrics']['complete_three_years'])
        self.assertAlmostEqual(r['metrics']['cagr_2y_pct'], 20.208153, places=5)

    def test_nonpositive_or_missing_years_never_create_cagr(self):
        for values in ([0, 1, 3], [-5, 2, 5], [5, -2, 10], [1, None, 2], [0, 0, 0]):
            result = metrics(values)
            self.assertIsNone(result['cagr_2y_pct'])
            self.assertIsNone(result['positive_path_coefficient_of_variation'])
        self.assertEqual(metrics([0, 0, 0])['positive_years'], 0)
        self.assertTrue(metrics([0, 0, 0])['complete_three_years'])

    def test_amount_missing_zero_parentheses_and_invalid_values(self):
        self.assertEqual(amount('(1,234.50)'), -1234.5)
        self.assertEqual(amount('0'), 0)
        self.assertIsNone(amount('-'))
        for value in ('NaN', 'Infinity', '1e4', '1,2', '1000000001', True, 'PRIVATE_TEXT'):
            with self.subTest(value=value), self.assertRaises(EPSError):
                amount(value)

    def test_diluted_custom_eps_and_account_name_are_never_substituted(self):
        for account in ('ifrs-full_DilutedEarningsLossPerShare', 'custom_basic_eps'):
            p = payload()
            p['list'][0].update(account_id=account, account_nm='기본주당이익')
            r = parse(p, corp='00123456', base=BASE, on_day=DAY)
            self.assertEqual(r['reported_basic_eps_2023_2025'], [None]*3)
            self.assertEqual(r['status'], 'standard_eps_not_observed')

    def test_wrong_issuer_year_scope_receipt_currency_and_quarter_fail(self):
        for field, value in (('corp_code', '99999999'), ('bsns_year', '2024'), ('fs_div', 'OFS'),
                             ('reprt_code', '11014'), ('rcept_no', '20260318000001'), ('currency', 'USD')):
            p = payload()
            p['list'][0][field] = value
            with self.subTest(field=field), self.assertRaises(EPSError):
                parse(p, corp='00123456', base=BASE, on_day=DAY)

    def test_duplicate_rows_must_agree_and_cannot_fill_missing_years_from_another_row(self):
        p = payload()
        p['list'].append(copy.deepcopy(p['list'][0]))
        self.assertTrue(parse(p, corp='00123456', base=BASE, on_day=DAY)['metrics']['complete_three_years'])
        p['list'][1]['frmtrm_amount'] = ''
        with self.assertRaises(EPSError):
            parse(p, corp='00123456', base=BASE, on_day=DAY)

    def test_missing_account_and_provider_error_are_different(self):
        r = parse({'status': '013'}, corp='00123456', base=BASE, on_day=DAY)
        self.assertEqual(r['status'], 'standard_eps_not_observed')
        for p in ({'status': '020'}, {'status': '000', 'list': []}, {'status': '000', 'list': 'private'}):
            with self.assertRaises(EPSError):
                parse(p, corp='00123456', base=BASE, on_day=DAY)

    def test_same_filing_cache_reused_on_new_market_date_but_correction_requeued(self):
        catalog, book = inputs()
        r = parse(payload(), corp='00123456', base=BASE, on_day=DAY)
        expected = {BASE['code']: BASE}
        prior = snapshot(DAY, catalog, book, expected, {BASE['code']: r})
        self.assertEqual(plan(catalog, book, prior)[-1], [])
        catalog['date'] = book['asof'] = '2026-11-04'
        self.assertEqual(plan(catalog, book, prior)[-1], [])
        book['records'][0]['annual']['receipt_id'] = '20260401000001'
        self.assertEqual(plan(catalog, book, prior)[-1], [BASE['code']])

    def test_tampered_metrics_and_certification_cannot_be_reused(self):
        catalog, book = inputs()
        r = parse(payload(), corp='00123456', base=BASE, on_day=DAY)
        original = snapshot(DAY, catalog, book, {BASE['code']: BASE}, {BASE['code']: r})
        for change in ('metrics', 'certification', 'date'):
            bad = copy.deepcopy(original)
            if change == 'metrics': bad['records'][BASE['code']]['metrics']['cagr_2y_pct'] = 999
            if change == 'certification': bad['investment_score_certified'] = True
            if change == 'date': bad['records'][BASE['code']]['observed_on'] = '2026-99-99'
            with self.subTest(change=change), self.assertRaises(EPSError):
                plan(catalog, book, bad)

    def test_collection_checkpoints_and_exhausted_queue_makes_zero_provider_calls(self):
        catalog, book = inputs()
        class Reply:
            def raise_for_status(self): pass
            def json(self): return payload()
        class Session:
            calls = []
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Reply()
        session, checkpoints = Session(), []
        with patch('v3_dart_reported_eps.verified_company_mapping_cache', return_value={BASE['code']: '00123456'}):
            result, calls = collect(catalog, book, None, key='a'*40, session=session, on_progress=checkpoints.append)
            second, repeated = collect(catalog, book, result, key='', session=session)
        self.assertEqual(calls, 1)
        self.assertEqual(repeated, 0)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(second, result)
        self.assertFalse(result['investment_score_certified'])


if __name__ == '__main__':
    unittest.main()
