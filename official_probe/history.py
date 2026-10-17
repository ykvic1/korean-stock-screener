"""Small OpenDART financial history research: never an as-of/backtest certification.

One company, up to three prior annual years plus first-quarter and half-year
reports. Financial endpoint can show revised data: matching a prior receipt
does not prove that its numeric fields equal the original historical version.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from .clients import canonical_date, ProbeError
from .financial import ACCOUNTS, _krw, sample_annual_financial

PERIODS = {
    '11013': ('분기보고서', '03', 'A003'),
    '11012': ('반기보고서', '06', 'A002'),
}
MAX_HISTORY_YEARS = 3


def _reported_amounts(rows, aliases, sections):
    chosen = [r for r in rows
              if r.get('account_id') in aliases
              and r.get('sj_div') in sections
              and r.get('account_detail', '-') in ('', '-')
              and r.get('currency') == 'KRW']
    fields = ('thstrm_amount', 'thstrm_add_amount')
    data = {}
    for field in fields:
        amounts = {_krw(row.get(field)) for row in chosen}
        amounts.discard(None)
        data[field + '_krw'] = next(iter(amounts)) if len(amounts) == 1 else None
        data[field + '_status'] = 'single_value' if len(amounts) == 1 else 'missing_or_ambiguous'
    data['matching_rows'] = len(chosen)
    data['quarter_only_derived'] = False
    data['scope'] = ('balance_sheet_point_in_time' if sections == ('BS',)
                     else 'source_fields_not_reclassified')
    return data


def sample_period_financial(client, corp_code: str, business_year: int,
                            report_code: str, asof: str, fs_div='CFS'):
    """One filing search and at most one financial request, with strict evidence gating."""
    if not re.fullmatch(r'\d{8}', str(corp_code)):
        raise ValueError('Invalid corp code')
    if report_code not in PERIODS or fs_div not in ('CFS', 'OFS'):
        raise ValueError('Invalid periodic report or accounting basis')
    if not 2015 <= business_year <= 2100:
        raise ValueError('Invalid business year')
    canonical_date(asof)
    label, month, detail = PERIODS[report_code]
    cutoff = date.fromisoformat(asof) - timedelta(days=1)
    result = {
        'business_year': business_year, 'report_code': report_code,
        'fs_div': fs_div, 'asof': asof,
        'status': 'unverified', 'metric_values': None,
        'receipt_matched_before_asof': False,
        'revision_history_validated': False,
        'asof_join_validated': False, 'investment_data_ready': False,
        'confirmed_scoring_allowed': False, 'single_quarter_calculated': False,
        'quarterly_growth_verified': False, 'accounts_returned': 0,
    }
    # This only avoids speculative early calls; actual publication date is checked below.
    report_period_end = date(business_year, int(month), 1)
    if cutoff < report_period_end:
        result['status'] = 'report_period_not_ended'
        return result

    listing = client._api(
        'list.json', corp_code=corp_code, pblntf_ty='A',
        pblntf_detail_ty=detail, last_reprt_at='N',
        bgn_de=f'{business_year}0101',
        end_de=cutoff.strftime('%Y%m%d'),
        page_no=1, page_count=100)
    if listing.get('status') == '013':
        result['status'] = 'no_report_before_asof'
        return result
    filings = listing.get('list')
    if not isinstance(filings, list):
        raise ProbeError('DART periodic filings list missing')
    try:
        pages = int(listing.get('total_page', 0))
    except (ValueError, TypeError):
        pages = 0
    if pages != 1:
        result['status'] = 'filing_search_incomplete'
        return result

    period = re.compile(r'\(\s*' + str(business_year) + r'\s*\.\s*' + month + r'\s*\)')
    eligible = {}
    for filing in filings:
        if not isinstance(filing, dict):
            continue
        receipt = str(filing.get('rcept_no', ''))
        received = str(filing.get('rcept_dt', ''))
        name = str(filing.get('report_nm', ''))
        if (re.fullmatch(r'\d{14}', receipt) and re.fullmatch(r'\d{8}', received)
                and receipt[:8] == received and received < asof.replace('-', '')
                and label in name and period.search(name)
                and str(filing.get('corp_code', corp_code)) == corp_code):
            eligible[receipt] = received
    result['eligible_filing_receipts'] = len(eligible)
    if not eligible:
        result['status'] = 'no_matching_period_filing'
        return result

    statement = client._api(
        'fnlttSinglAcntAll.json', corp_code=corp_code,
        bsns_year=str(business_year), reprt_code=report_code, fs_div=fs_div)
    if statement.get('status') == '013':
        result['status'] = 'statement_not_available'
        return result
    rows = statement.get('list')
    if not isinstance(rows, list):
        raise ProbeError('DART quarterly statement list missing')
    result['accounts_returned'] = len(rows)
    valid_rows = (bool(rows) and all(
        isinstance(r, dict)
        and str(r.get('corp_code', corp_code)) == corp_code
        and str(r.get('bsns_year')) == str(business_year)
        and str(r.get('reprt_code')) == report_code
        and str(r.get('fs_div', fs_div)) == fs_div
        for r in rows))
    receipts = {str(r.get('rcept_no', '')) for r in rows if isinstance(r, dict)}
    if not valid_rows or len(receipts) != 1:
        result['status'] = 'statement_schema_or_receipt_inconsistent'
        return result
    receipt = next(iter(receipts))
    if receipt not in eligible:
        result['status'] = 'statement_receipt_not_visible_asof'
        return result

    result['receipt_matched_before_asof'] = True
    result['matched_receipt'] = receipt
    result['matched_receipt_date'] = eligible[receipt]
    result['metric_values'] = {
        metric: _reported_amounts(rows, aliases, sections)
        for metric, (aliases, sections) in ACCOUNTS.items()
    }
    result['status'] = 'observed_receipt_matched_revision_unverified'
    return result


def _cfs_then_ofs(call):
    cfs = call('CFS')
    if cfs['status'] not in ('annual_statement_not_available', 'statement_not_available'):
        return cfs
    ofs = call('OFS')
    ofs['used_OFS_fallback'] = True
    return ofs


def sample_history(client, corp_code: str, asof: str, years: int = 3):
    """Fixed small sample, no retries, caching, API expansion, scores or publication."""
    canonical_date(asof)
    if not isinstance(years, int) or not 1 <= years <= MAX_HISTORY_YEARS:
        raise ValueError('History sample supports only one to three prior years')
    asof_year = date.fromisoformat(asof).year
    annual = []
    for y in range(asof_year - 1, asof_year - years - 1, -1):
        annual.append(_cfs_then_ofs(
            lambda basis, y=y: sample_annual_financial(
                client, corp_code, y, asof, fs_div=basis)))
    period = []
    for code in ('11013', '11012'):
        period.append(_cfs_then_ofs(
            lambda basis, code=code: sample_period_financial(
                client, corp_code, asof_year, code, asof, fs_div=basis)))
    bases = {r['fs_div'] for r in annual
             if r['status'] == 'observed_receipt_matched_revision_unverified'}
    all_observed = all(r['status'] == 'observed_receipt_matched_revision_unverified'
                       for r in annual + period)
    return {
        'asof': asof, 'annual_years_requested': years,
        'annual': annual, 'current_year_periods': period,
        'annual_scope_consistent': len(bases) == 1 and len(annual) == years and all_observed,
        'accounting_bases_seen': sorted(bases),
        'flow_quarter_values_derived': False,
        'revision_history_validated': False,
        'asof_join_validated': False, 'investment_data_ready': False,
        'confirmed_scoring_allowed': False,
        'status': 'observed_research_only' if all_observed else 'partial_or_unverified',
        'notes': [
            'Annual values are current API observations, not verified historical versions.',
            'Quarter/H1 source thstrm_amount and thstrm_add_amount remain separate.',
            'No quarterly differences, growth, valuation or investment scores computed.',
        ],
    }
