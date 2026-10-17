"""Strictly bounded OpenDART annual-financial sample, never PIT/backtest certified.

The finance endpoint can serve subsequently restated numbers. A matching pre-asof
filing receipt is useful evidence, NOT sufficient proof of a historical snapshot.
Only whitelisted KRW accounts with one unambiguous value are emitted.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from .clients import ProbeError, canonical_date

REPORT_CODE = '11011'
ACCOUNTS = {
    'revenue': (('ifrs-full_Revenue', 'ifrs_Revenue', 'ifrs-full_RevenueFromContractsWithCustomers'), ('IS', 'CIS')),
    'operating_income': (('dart_OperatingIncomeLoss',), ('IS', 'CIS')),
    'operating_cashflow': (('ifrs-full_CashFlowsFromUsedInOperatingActivities',
                           'ifrs_CashFlowsFromUsedInOperatingActivities'), ('CF',)),
    'assets': (('ifrs-full_Assets', 'ifrs_Assets'), ('BS',)),
    'liabilities': (('ifrs-full_Liabilities', 'ifrs_Liabilities'), ('BS',)),
    'equity': (('ifrs-full_Equity', 'ifrs_Equity'), ('BS',)),
}


def _krw(value):
    """Parse a whole-KRW statement amount; no silent currency/scale inference."""
    raw = str(value).strip()
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)', raw):
        return None
    return int(raw.replace(',', ''))


def _metric(rows, aliases, sections):
    matched = [r for r in rows if r.get('account_id') in aliases
               and r.get('sj_div') in sections
               and r.get('account_detail', '-') in ('', '-')
               and r.get('currency') == 'KRW']
    amounts = {_krw(r.get('thstrm_amount')) for r in matched}
    amounts.discard(None)
    if len(amounts) != 1:
        return {'amount_krw': None, 'status': 'missing_or_ambiguous',
                'matching_rows': len(matched)}
    amount = next(iter(amounts))
    return {'amount_krw': amount, 'status': 'observed_not_pit_validated',
            'matching_rows': len(matched)}


def sample_annual_financial(client, corp_code: str, business_year: int,
                            asof: str, fs_div: str = 'CFS') -> dict:
    """At most 2 DART JSON calls; independent as-of evidence is NOT asserted."""
    if not re.fullmatch(r'\d{8}', str(corp_code)):
        raise ValueError('corp_code must be 8 digits')
    if fs_div not in ('CFS', 'OFS') or not 2015 <= business_year <= 2100:
        raise ValueError('Invalid annual financial sample parameters')
    canonical_date(asof)
    cutoff = date.fromisoformat(asof) - timedelta(days=1)  # same day always excluded
    first_filing_day = date(business_year + 1, 1, 1)
    output = {'business_year': business_year, 'fs_div': fs_div, 'report_code': REPORT_CODE,
              'asof': asof, 'receipt_matched_before_asof': False,
              'asof_join_validated': False, 'revision_history_validated': False,
              'investment_data_ready': False, 'metric_values': None,
              'financial_metrics_verified': False, 'annual_filings_first_page': 0,
              'accounts_returned': 0, 'status': 'unverified'}
    if cutoff < first_filing_day:
        output['status'] = 'annual_report_not_yet_eligible'
        return output

    filings = client._api(
        'list.json', corp_code=corp_code,
        bgn_de=first_filing_day.strftime('%Y%m%d'),
        end_de=cutoff.strftime('%Y%m%d'),
        pblntf_ty='A', pblntf_detail_ty='A001',
        last_reprt_at='N', page_no=1, page_count=100)
    if filings.get('status') == '013':
        output['status'] = 'no_annual_filing_before_asof'
        return output
    items = filings.get('list')
    if not isinstance(items, list):
        raise ProbeError('OpenDART annual filing list missing')
    output['annual_filings_first_page'] = len(items)
    # Partial search results cannot prove absence of later/pre-asof revisions.
    try:
        pages = int(filings.get('total_page', 0))
    except (ValueError, TypeError):
        pages = 0
    if pages != 1:
        output['status'] = 'annual_filing_search_incomplete'
        return output

    valid = {}
    for r in items:
        if not isinstance(r, dict):
            continue
        receipt = str(r.get('rcept_no', ''))
        received = str(r.get('rcept_dt', ''))
        name = str(r.get('report_nm', ''))
        if (re.fullmatch(r'\d{14}', receipt)
                and re.fullmatch(r'\d{8}', received)
                and receipt[:8] == received
                and received < asof.replace('-', '')
                and '사업보고서' in name
                and str(business_year) in name
                and str(r.get('corp_code', corp_code)) == corp_code):
            valid[receipt] = received
    if not valid:
        output['status'] = 'no_matching_annual_filing_receipt'
        return output

    statement = client._api(
        'fnlttSinglAcntAll.json', corp_code=corp_code,
        bsns_year=str(business_year), reprt_code=REPORT_CODE, fs_div=fs_div)
    if statement.get('status') == '013':
        output['status'] = 'annual_statement_not_available'
        return output
    rows = statement.get('list')
    if not isinstance(rows, list):
        raise ProbeError('OpenDART financial statement list missing')
    output['accounts_returned'] = len(rows)
    receipts = {str(r.get('rcept_no', '')) for r in rows if isinstance(r, dict)}
    consistent = (bool(rows) and all(isinstance(r, dict)
                  and str(r.get('corp_code', corp_code)) == corp_code
                  and str(r.get('bsns_year')) == str(business_year)
                  and str(r.get('reprt_code')) == REPORT_CODE
                  and str(r.get('fs_div', fs_div)) == fs_div
                  for r in rows))
    if not consistent or len(receipts) != 1:
        output['status'] = 'annual_statement_schema_or_receipt_inconsistent'
        return output
    receipt = next(iter(receipts))
    if receipt not in valid:
        output['status'] = 'annual_statement_receipt_not_matched_asof'
        return output

    output['receipt_matched_before_asof'] = True
    output['matched_receipt'] = receipt
    output['matched_receipt_date'] = valid[receipt]
    output['metric_values'] = {
        name: _metric(rows, ids, sections)
        for name, (ids, sections) in ACCOUNTS.items()
    }
    # This endpoint may be a current, subsequently restated view of a filing.
    # Receipt + date matching is NOT historical-version reconstruction.
    output['status'] = 'observed_receipt_matched_revision_unverified'
    return output
