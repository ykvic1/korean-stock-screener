"""Offline-only annual filing revision evidence tests; no API keys or network."""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from official_probe.__main__ import run
from official_probe.clients import DARTClient, ProbeError
from official_probe.revisions import (
    _zip_evidence, audit_annual_revisions,
)

CORP = '00126380'
ASOF = '2026-10-16'
YEAR = 2025
ORIGINAL = '20260316001120'
AMENDED = '20260715000005'


def row(receipt=ORIGINAL, name='사업보고서 (2025.12)', day=None, corp=CORP):
    return {'rcept_no': receipt, 'rcept_dt': day or receipt[:8],
            'corp_code': corp, 'report_nm': name}


def response(*filings, pages=1):
    return {'status': '000', 'list': list(filings), 'total_page': str(pages)}


def archive(content: bytes = b'<REPORT><TABLE>123</TABLE></REPORT>',
            name='report.xml'):
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as f:
        f.writestr(name, content)
    return buffer.getvalue()


class FakeClient:
    def __init__(self, listings, docs=None):
        self.listings = listings
        self.docs = docs if docs is not None else {}
        self.api_calls = []
        self.document_calls = []

    def _api(self, endpoint, **kwargs):
        self.api_calls.append((endpoint, kwargs))
        assert endpoint == 'list.json'
        return self.listings

    def original_document_zip(self, receipt):
        self.document_calls.append(receipt)
        thing = self.docs[receipt]
        if isinstance(thing, BaseException):
            raise thing
        return thing


class AnnualRevisionAuditTests(unittest.TestCase):
    def test_single_document_observed_but_not_numeric_verified(self):
        fake = FakeClient(response(row()), {ORIGINAL: archive()})
        out = audit_annual_revisions(fake, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['status'],
                         'single_filing_observed_numeric_revision_unverified')
        self.assertTrue(out['expected_receipt_latest_asof'])
        self.assertTrue(out['revision_listing_complete'])
        self.assertFalse(out['amendment_observed'])
        self.assertEqual(out['version_count'], 1)
        self.assertEqual(fake.document_calls, [ORIGINAL])
        self.assertEqual(len(fake.api_calls), 1)
        endpoint, params = fake.api_calls[0]
        self.assertEqual(endpoint, 'list.json')
        self.assertEqual(params['last_reprt_at'], 'N')
        self.assertEqual(params['pblntf_detail_ty'], 'A001')
        self.assertEqual(params['bgn_de'], '20260101')
        self.assertEqual(params['end_de'], '20261015')
        self.assertFalse(out['financial_numeric_changes_verified'])
        self.assertFalse(out['revision_history_validated'])
        self.assertFalse(out['investment_data_ready'])
        self.assertFalse(out['asof_join_validated'])
        self.assertFalse(out['confirmed_scoring_allowed'])

    def test_amendment_document_hash_diff_is_not_numeric_diff(self):
        fake = FakeClient(
            response(row(AMENDED, '[기재정정]사업보고서 (2025.12)'), row()),
            {ORIGINAL: archive(b'<A>111</A>'),
             AMENDED: archive(b'<A>222</A>')})
        out = audit_annual_revisions(fake, CORP, YEAR, ASOF, AMENDED)
        self.assertEqual(out['status'],
                         'revision_versions_observed_numeric_diff_unverified')
        self.assertTrue(out['amendment_observed'])
        self.assertEqual(out['version_count'], 2)
        self.assertTrue(out['expected_receipt_latest_asof'])
        self.assertFalse(out['document_content_equal'])
        self.assertEqual(fake.document_calls, [ORIGINAL, AMENDED])
        self.assertEqual(out['versions'][0]['rcept_no'], ORIGINAL)
        self.assertEqual(out['versions'][1]['filing_kind'], 'correction_labeled')
        self.assertFalse(out['historical_numeric_snapshot_verified'])

    def test_expected_financial_receipt_superseded(self):
        fake = FakeClient(
            response(row(), row(AMENDED, '[정정]사업보고서 (2025.12)')),
            {ORIGINAL: archive(b'A'), AMENDED: archive(b'B')})
        out = audit_annual_revisions(fake, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['status'], 'financial_receipt_superseded_asof')
        self.assertFalse(out['expected_receipt_latest_asof'])
        self.assertTrue(out['expected_receipt_in_history'])

    def test_archive_raw_zip_hash_diff_can_have_same_inner_contents(self):
        # ZIP archive may change due to additional zip comment, not numeric data.
        first = archive(b'<same/>')
        second = first + b'extra ZIP comment bytes'
        a, b = _zip_evidence(first), _zip_evidence(second)
        self.assertNotEqual(a['archive_sha256'], b['archive_sha256'])
        self.assertEqual(a['internal_content_manifest_sha256'],
                         b['internal_content_manifest_sha256'])

    def test_expected_receipt_not_present_is_blocker(self):
        fake = FakeClient(response(row()), {ORIGINAL: archive()})
        out = audit_annual_revisions(fake, CORP, YEAR, ASOF, AMENDED)
        self.assertEqual(out['status'], 'expected_financial_receipt_not_in_filing_history')
        self.assertFalse(fake.document_calls)
        self.assertFalse(out['revision_history_validated'])

    def test_pagination_incomplete_never_downloads_document(self):
        fake = FakeClient(response(row(), pages=2))
        out = audit_annual_revisions(fake, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['status'], 'filing_list_pagination_incomplete')
        self.assertFalse(out['revision_listing_complete'])
        self.assertFalse(fake.document_calls)

    def test_unexpected_filing_date_or_company_is_blocked(self):
        for item in [
            row(day='20261016'), row(corp='00000000'),
            row(receipt=ORIGINAL, day='20260317'),
        ]:
            with self.subTest(item=item):
                c = FakeClient(response(item))
                out = audit_annual_revisions(c, CORP, YEAR, ASOF, ORIGINAL)
                self.assertEqual(out['status'], 'invalid_or_duplicate_target_filing_row')
                self.assertFalse(c.document_calls)

    def test_duplicate_receipt_is_blocked(self):
        c = FakeClient(response(row(), row()))
        out = audit_annual_revisions(c, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['status'], 'invalid_or_duplicate_target_filing_row')
        self.assertFalse(c.document_calls)

    def test_unrelated_period_is_ignored_not_classified_as_amendment(self):
        c = FakeClient(response(row(), row('20260516000001','분기보고서 (2026.03)')),
                       {ORIGINAL: archive()})
        out = audit_annual_revisions(c, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['version_count'], 1)
        self.assertFalse(out['amendment_observed'])

    def test_no_report_status_does_not_download(self):
        c = FakeClient({'status':'013','list':[]})
        out = audit_annual_revisions(c, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['status'], 'no_filings_in_search_window')
        self.assertFalse(c.document_calls)

    def test_document_missing_marks_partial_and_never_claims_certification(self):
        c = FakeClient(response(row()), {ORIGINAL: ProbeError('Document unavailable')})
        out = audit_annual_revisions(c, CORP, YEAR, ASOF, ORIGINAL)
        self.assertEqual(out['status'], 'document_sample_incomplete')
        self.assertEqual(out['original_document_samples'][0]['status'],
                         'document_unverified')
        self.assertIsNone(out['document_content_equal'])
        self.assertFalse(out['financial_numeric_changes_verified'])

    def test_zip_archive_limits_and_path_traversal(self):
        with self.assertRaises(ProbeError):
            _zip_evidence(b'not-a-zip-file')
        with self.assertRaises(ProbeError):
            _zip_evidence(archive(name='../report.xml'))
        with self.assertRaises(ProbeError):
            _zip_evidence(archive(name='absolute\\evil.xml'))
        with self.assertRaises(ProbeError):
            _zip_evidence(archive(content=b''))
        with self.assertRaises(ProbeError):
            _zip_evidence(archive(content=b'x' * 100, name='/root.xml'))

    def test_list_only_mode_never_requests_original_documents(self):
        c = FakeClient(response(row()))
        out = audit_annual_revisions(c, CORP, YEAR, ASOF, ORIGINAL,
                                     download_documents=False)
        self.assertEqual(out['status'], 'listing_observed_documents_not_requested')
        self.assertFalse(c.document_calls)

    def test_invalid_expected_receipt_is_preflight_error_without_requests(self):
        c = FakeClient(response(row()))
        for receipt in ('123', ORIGINAL + '3', 'bad'):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                audit_annual_revisions(c, CORP, YEAR, ASOF, receipt)
        self.assertFalse(c.api_calls)

    def test_cli_rejects_unapproved_or_ambiguous_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data.json'
            data.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code':'252990','market':'KOSDAQ'},
                {'code':'491000','market':'KOSDAQ'}]}), encoding='utf-8')
            base = ['--asof', ASOF, '--provider', 'dart', '--revision-audit',
                    '--source', str(data),
                    '--revision-expected-receipt', ORIGINAL]
            invalid = [
                base + ['--symbols', '252990'],  # no --live
                base + ['--live', '--symbols', '252990,491000'],
                base + ['--live'],  # no explicit selection
                base + ['--live', '--symbols', '252990', '--history-sample'],
                base + ['--live', '--symbols', '252990', '--finance-sample'],
            ]
            for argv in invalid:
                with self.subTest(argv=argv), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        run(argv)

    def test_cli_no_key_preserves_secret_and_does_not_call_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data.json'
            output = Path(tmp) / 'audit.json'
            data.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code':'252990','market':'KOSDAQ'}]}), encoding='utf-8')
            with patch.dict(os.environ, {'DART_API_KEY': ''}):
                out = run(['--asof', ASOF, '--provider', 'dart', '--live',
                           '--revision-audit', '--revision-expected-receipt',
                           ORIGINAL, '--symbols','252990',
                           '--source',str(data),'--output',str(output)])
            self.assertEqual(out['dart']['status'], 'key_required')
            self.assertFalse(out['investment_data_ready'])
            self.assertTrue(output.exists())

    def test_client_zip_url_param_does_not_print_secret(self):
        class Response:
            status_code = 200
            content = archive(b'<r/>')
        class Session:
            def __init__(self):
                self.calls = []
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()
        sess = Session()
        raw = DARTClient('TOP_SECRET', session=sess).original_document_zip(ORIGINAL)
        self.assertEqual(raw, Response.content)
        url, kw = sess.calls[0]
        self.assertTrue(url.endswith('/document.xml'))
        self.assertNotIn('TOP_SECRET', url)
        self.assertEqual(kw['params']['rcept_no'], ORIGINAL)
        self.assertFalse(kw['allow_redirects'])
        with self.assertRaises(ValueError):
            DARTClient('TOP_SECRET', session=sess).original_document_zip('1')


if __name__ == '__main__':
    unittest.main()
