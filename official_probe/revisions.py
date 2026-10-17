"""Bounded, non-publishing annual disclosure revision evidence audit.

Filing-list history and per-receipt original documents are a prerequisite for
historical as-of reconstruction. This module never labels financial numbers as
PIT verified or interprets an archive change as a numeric restatement.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import re
from zipfile import ZipFile, BadZipFile

from .clients import ProbeError, canonical_date

MAX_DOC_ENTRIES = 200
MAX_UNCOMPRESSED_TOTAL = 80_000_000
MAX_UNCOMPRESSED_SINGLE = 45_000_000
MAX_DOCUMENT_VERSIONS = 2


def _zip_evidence(blob: bytes) -> dict:
    """Validate one original ZIP and hash its content in memory (no raw persist).

    Hash both raw ZIP and ordered internal file bytes. ZIP timestamp differences
    can change raw hash without changing internal file content.
    """
    if len(blob) > 25_000_000 or len(blob) < 8:
        raise ProbeError('Disclosure archive size outside bounded probe limit')
    try:
        with ZipFile(BytesIO(blob)) as archive:
            entries = [i for i in archive.infolist() if not i.is_dir()]
            if not entries or len(entries) > MAX_DOC_ENTRIES:
                raise ProbeError('Disclosure archive has invalid entry count')
            seen = set()
            total = 0
            for member in entries:
                name = member.filename
                path = PurePosixPath(name)
                if (not name or name.startswith('/')
                        or '\\' in name or path.is_absolute()
                        or '..' in path.parts or name in seen):
                    raise ProbeError('Disclosure archive contains unsafe or duplicate paths')
                seen.add(name)
                if member.file_size > MAX_UNCOMPRESSED_SINGLE:
                    raise ProbeError('Disclosure archive entry exceeds size limit')
                total += member.file_size
                if total > MAX_UNCOMPRESSED_TOTAL:
                    raise ProbeError('Disclosure archive extraction exceeds size limit')
            if total == 0:
                raise ProbeError('Disclosure archive contains no data')
            manifest = sha256()
            for member in sorted(entries, key=lambda x: x.filename):
                digest = sha256()
                length = 0
                with archive.open(member) as handle:
                    while True:
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        length += len(chunk)
                        if length > member.file_size or length > MAX_UNCOMPRESSED_SINGLE:
                            raise ProbeError('Disclosure archive streaming size mismatch')
                        digest.update(chunk)
                if length != member.file_size:
                    raise ProbeError('Disclosure archive entry size mismatch')
                manifest.update(member.filename.encode('utf-8', 'surrogatepass'))
                manifest.update(b'\0')
                manifest.update(str(length).encode())
                manifest.update(b'\0')
                manifest.update(digest.hexdigest().encode())
                manifest.update(b'\n')
            return {
                'status': 'document_archive_hashed',
                'archive_sha256': sha256(blob).hexdigest(),
                'internal_content_manifest_sha256': manifest.hexdigest(),
                'entry_count': len(entries),
                'uncompressed_bytes': total,
                'raw_document_retained': False,
                'financial_numeric_changes_verified': False,
            }
    except (BadZipFile, RuntimeError, OSError, UnicodeError, ValueError, EOFError):
        raise ProbeError('Disclosure ZIP is unreadable or invalid') from None


def _base(corp_code, business_year, asof, expected_receipt):
    return {
        'corp_code': corp_code, 'business_year': business_year,
        'asof': asof, 'cutoff_policy': 'receipt_date_strictly_before_asof',
        'expected_financial_receipt': expected_receipt,
        'requested_disclosure_type': 'annual A001 with all amendments',
        'status': 'not_verified', 'revision_listing_complete': False,
        'versions': [], 'version_count': 0,
        'amendment_observed': None, 'expected_receipt_in_history': False,
        'expected_receipt_latest_asof': False,
        'original_document_samples': [], 'document_content_equal': None,
        'financial_numeric_changes_verified': False,
        'historical_numeric_snapshot_verified': False,
        'revision_history_validated': False,
        'asof_join_validated': False,
        'confirmed_scoring_allowed': False,
        'investment_data_ready': False,
        'warnings': [
            'Document content differences are not proof of financial-statement value changes.',
            'Current financial API rows cannot reconstruct an earlier filing revision.',
            'Receipt date without disclosure time cannot validate same-day trading availability.',
            'This audit covers one business year up to the asof cutoff, not future amendments.',
        ],
    }


def audit_annual_revisions(client, corp_code: str, business_year: int,
                           asof: str, expected_receipt: str,
                           *, download_documents: bool = True) -> dict:
    """One listing request + <=2 original-document ZIP downloads; no retries.

    The expected receipt must come from an independently recorded financial
    sample, not guessed from a filing list. Never call the live API implicitly.
    """
    if not re.fullmatch(r'\d{8}', str(corp_code)):
        raise ValueError('corp_code must be eight digits')
    if not isinstance(business_year, int) or not 2015 <= business_year <= 2100:
        raise ValueError('Invalid annual business year')
    if not re.fullmatch(r'\d{14}', str(expected_receipt)):
        raise ValueError('Prior financial receipt must be 14 digits')
    canonical_date(asof)
    cutoff = date.fromisoformat(asof) - timedelta(days=1)
    first = date(business_year + 1, 1, 1)
    result = _base(corp_code, business_year, asof, expected_receipt)
    if cutoff < first:
        result['status'] = 'annual_filing_not_yet_possible'
        return result

    response = client._api(
        'list.json', corp_code=corp_code, pblntf_ty='A',
        pblntf_detail_ty='A001', last_reprt_at='N',
        bgn_de=first.strftime('%Y%m%d'),
        end_de=cutoff.strftime('%Y%m%d'),
        page_no=1, page_count=100)
    if response.get('status') == '013':
        result['status'] = 'no_filings_in_search_window'
        return result
    items = response.get('list')
    if not isinstance(items, list) or len(items) > 100:
        raise ProbeError('OpenDART filing list returned unexpected shape')
    try:
        pages = int(response.get('total_page', 0))
    except (ValueError, TypeError):
        pages = 0
    if pages != 1:
        result['status'] = 'filing_list_pagination_incomplete'
        return result

    expression = re.compile(
        r'사업보고서\s*\(\s*' + str(business_year) + r'\s*\.\s*12\s*\)')
    versions = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            result['status'] = 'unexpected_filing_row'
            return result
        name = str(item.get('report_nm', ''))
        if not expression.search(name):
            continue
        receipt = str(item.get('rcept_no', ''))
        received = str(item.get('rcept_dt', ''))
        if (not re.fullmatch(r'\d{14}', receipt)
                or not re.fullmatch(r'\d{8}', received)
                or receipt[:8] != received
                or received < first.strftime('%Y%m%d')
                or received >= asof.replace('-', '')
                or str(item.get('corp_code', corp_code)) != corp_code
                or receipt in seen):
            result['status'] = 'invalid_or_duplicate_target_filing_row'
            return result
        seen.add(receipt)
        heading = name[:name.find('사업보고서')]
        versions.append({
            'rcept_no': receipt,
            'rcept_dt': received,
            'filing_kind': 'correction_labeled' if '정정' in heading
                           else 'original_or_unlabeled',
        })
    versions.sort(key=lambda v: (v['rcept_dt'], v['rcept_no']))
    result['revision_listing_complete'] = True
    result['versions'] = versions
    result['version_count'] = len(versions)
    result['amendment_observed'] = (
        len(versions) > 1
        or any(v['filing_kind'] == 'correction_labeled' for v in versions))
    if not versions:
        result['status'] = 'target_annual_report_not_in_list'
        return result

    result['expected_receipt_in_history'] = any(
        v['rcept_no'] == expected_receipt for v in versions)
    result['expected_receipt_latest_asof'] = (
        versions[-1]['rcept_no'] == expected_receipt)
    if not result['expected_receipt_in_history']:
        result['status'] = 'expected_financial_receipt_not_in_filing_history'
        return result

    if not download_documents:
        result['status'] = 'listing_observed_documents_not_requested'
        return result

    selected = [versions[0]]
    if versions[-1]['rcept_no'] != versions[0]['rcept_no']:
        selected.append(versions[-1])
    if len(selected) > MAX_DOCUMENT_VERSIONS:
        raise ValueError('Document version safety bound exceeded')

    for item in selected:
        receipt = item['rcept_no']
        try:
            blob = client.original_document_zip(receipt)
            evidence = _zip_evidence(blob)
            result['original_document_samples'].append({
                'rcept_no': receipt, **evidence})
        except (ProbeError, ValueError):
            result['original_document_samples'].append({
                'rcept_no': receipt, 'status': 'document_unverified',
                'error': 'Official document response invalid, inaccessible or beyond limits',
                'financial_numeric_changes_verified': False})
    good = [v for v in result['original_document_samples']
            if v['status'] == 'document_archive_hashed']
    if len(selected) == 2 and len(good) == 2:
        result['document_content_equal'] = (
            good[0]['internal_content_manifest_sha256']
            == good[1]['internal_content_manifest_sha256'])
    if len(good) != len(selected):
        result['status'] = 'document_sample_incomplete'
    elif not result['expected_receipt_latest_asof']:
        result['status'] = 'financial_receipt_superseded_asof'
    elif result['amendment_observed']:
        result['status'] = 'revision_versions_observed_numeric_diff_unverified'
    else:
        result['status'] = 'single_filing_observed_numeric_revision_unverified'
    return result
