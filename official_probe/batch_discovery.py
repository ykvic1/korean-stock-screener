"""Manual-only annual receipt discovery for a bounded XBRL pilot cohort.

This mode NEVER downloads financial statements or claims PIT eligibility.
A discovered receipt is only a candidate requiring separate scope/API review.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from .clients import ProbeError, canonical_date

MAX_PILOT_VERSIONS = 100


def discover_annual_receipt(client, corp_code: str, business_year: int, asof: str) -> dict:
    if not re.fullmatch(r'\d{8}', str(corp_code)):
        raise ValueError('DART corp_code must be eight digits')
    if type(business_year) is not int or not 2015 <= business_year <= 2100:
        raise ValueError('Business year out of supported range')
    canonical_date(asof)
    cutoff = date.fromisoformat(asof) - timedelta(days=1)
    first = date(business_year + 1, 1, 1)
    out = {
        'status': 'not_verified', 'business_year': business_year,
        'asof': asof, 'corp_code': corp_code,
        'receipt_candidate': None, 'versions': [],
        'revision_listing_complete': False,
        'scope_choice_required': True,
        'financial_api_receipt_matches': False,
        'xbrl_accounting_scope_verified': False,
        'historical_numeric_snapshot_verified': False,
        'revision_history_validated': False,
        'asof_join_validated': False,
        'confirmed_scoring_allowed': False,
        'investment_data_ready': False,
    }
    if cutoff < first:
        out['status'] = 'filing_window_not_yet_open'
        return out
    response = client._api(
        'list.json', corp_code=corp_code, pblntf_ty='A',
        pblntf_detail_ty='A001', last_reprt_at='N',
        bgn_de=first.strftime('%Y%m%d'),
        end_de=cutoff.strftime('%Y%m%d'),
        page_no=1, page_count=100)
    if response.get('status') == '013':
        out['status'] = 'no_report_in_window'
        return out
    items = response.get('list')
    if not isinstance(items, list) or len(items) > MAX_PILOT_VERSIONS:
        raise ProbeError('OpenDART annual receipt discovery list is invalid or oversized')
    try:
        pages = int(response.get('total_page', 0))
    except (TypeError, ValueError):
        pages = 0
    if pages != 1:
        out['status'] = 'filing_listing_incomplete'
        return out
    pattern = re.compile(r'사업보고서\s*\(\s*' + str(business_year) + r'\s*\.\s*12\s*\)')
    versions = []
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            out['status'] = 'unexpected_filing_row'
            return out
        title = str(row.get('report_nm', ''))
        if not pattern.search(title):
            continue
        receipt = str(row.get('rcept_no', ''))
        received = str(row.get('rcept_dt', ''))
        if (not re.fullmatch(r'\d{14}', receipt)
                or not re.fullmatch(r'\d{8}', received)
                or received != receipt[:8]
                or received < first.strftime('%Y%m%d')
                or received >= asof.replace('-', '')
                or str(row.get('corp_code', corp_code)) != corp_code
                or receipt in seen):
            out['status'] = 'filing_row_identity_invalid_or_duplicate'
            return out
        try:
            date.fromisoformat(received[:4] + '-' + received[4:6] + '-' + received[6:8])
        except ValueError:
            out['status'] = 'filing_row_date_invalid'
            return out
        seen.add(receipt)
        prefix = title[:title.find('사업보고서')]
        versions.append({'rcept_no': receipt, 'rcept_dt': received,
                         'correction_labeled': '정정' in prefix})
    versions.sort(key=lambda x: (x['rcept_dt'], x['rcept_no']))
    out['versions'] = versions
    out['version_count'] = len(versions)
    out['revision_listing_complete'] = True
    if not versions:
        out['status'] = 'no_2025_annual_report_match'
    elif len(versions) != 1 or versions[0]['correction_labeled']:
        out['status'] = 'multiple_or_amended_reports_require_manual_review'
    else:
        out['status'] = 'single_original_annual_receipt_candidate_unverified'
        out['receipt_candidate'] = versions[0]['rcept_no']
    return out
