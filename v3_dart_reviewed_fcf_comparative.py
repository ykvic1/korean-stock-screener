"""Use FY2025 DART comparative statements for otherwise missing FY2023 FCF."""
from datetime import date
import re

from v3_dart_bulk_history import _midrank, _valid_receipt
from v3_dart_cashflow_factor_preview import real_features

OUTPUT = 'v3-dart-reviewed-fcf-comparative.json'
SCHEMA = 'v3-dart-reviewed-fcf-comparative-0.1'
YEARS = ('2023', '2024', '2025')
AMOUNTS = {
    'operating_cashflow_krw', 'ppe_cash_payments_krw',
    'intangible_cash_payments_krw', 'assets_krw',
    'investing_cashflow_krw', 'other_investing_cashflow_krw',
}


def verified_ratio(entry, history, cash, cutoff):
    """Recheck the two saved annual anchors and both FY2025 statement links."""
    scope = entry['scope']
    years = entry['years']
    receipts = entry['anchor_receipts']
    if (scope not in ('CFS', 'OFS') or cash.get('scope') != scope
            or set(years) != set(YEARS) or set(receipts) != {'2024', '2025'}):
        return None
    source_prefix = f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipts['2025']}&"
    statements = [entry[k] for k in ('cashflow_statement', 'balance_sheet')]
    if any(not s['url'].startswith(source_prefix)
           or not re.fullmatch('[0-9a-f]{64}', s['sha256']) for s in statements):
        return None
    if statements[0]['url'] == statements[1]['url'] or statements[0]['sha256'] == statements[1]['sha256']:
        return None

    ratios = []
    for year in YEARS:
        amounts = years[year]
        if set(amounts) != AMOUNTS or any(
                type(v) is not int or abs(v) > 10**18 for v in amounts.values()):
            return None
        operating = amounts['operating_cashflow_krw']
        ppe = amounts['ppe_cash_payments_krw']
        intangible = amounts['intangible_cash_payments_krw']
        assets = amounts['assets_krw']
        fcf = operating - ppe - intangible
        if (assets <= 0 or ppe < 0 or intangible < 0
                or amounts['other_investing_cashflow_krw'] - ppe - intangible
                   != amounts['investing_cashflow_krw']):
            return None
        if year in receipts:
            receipt = receipts[year]
            major = history.get('years', {}).get(year, {}).get(scope, {})
            observed = cash.get('years', {}).get(year, {})
            if (not _valid_receipt(receipt, int(year), cutoff)
                    or major.get('receipt_id') != receipt
                    or observed.get('annual_receipt_id') != receipt
                    or observed.get('major_annual_receipt_matched') is not True
                    or observed.get('scope') != scope
                    or major.get('values', {}).get('assets') != assets
                    or any(observed.get(key) != value for key, value in (
                        ('operating_cashflow_krw', operating), ('ppe_capex_krw', ppe),
                        ('intangible_capex_krw', intangible), ('free_cashflow_krw', fcf)))):
                return None
        ratios.append(100 * fcf / assets)
    ratio = round(sum(ratios) / len(YEARS), 6)
    return ratio if abs(ratio) <= 1e6 else None


def comparative_fcf_parts(catalog, history, cash, reviewed):
    """Score reviewed rows against the same-market FCF/assets observations."""
    day = catalog['date']
    if cash is None or reviewed is None or history.get('asof') != day or cash.get('asof') != day:
        return {}
    if reviewed.get('schema') != SCHEMA or not isinstance(reviewed.get('records'), dict):
        raise ValueError('invalid_reviewed_fcf_comparative_registry')

    # The latest filing receipt determines when the comparison was public.
    # The review date records when we extracted it, independently of score asof.
    cutoff = date.fromisoformat(day)
    markets = {row['code']: row['market'] for row in catalog['rows']}
    observed = real_features(catalog, history, cash)
    ratios = {code: values['financial_fcf_assets_3y_pct']
              for code, values in observed.items() if code in markets
              and values['financial_fcf_assets_3y_pct'] is not None}
    recovered = {}
    for code, entry in reviewed['records'].items():
        if code not in markets or code in ratios:
            continue
        ratio = verified_ratio(entry, history['records'].get(code, {}),
                               cash['records'].get(code, {}), cutoff)
        if ratio is not None:
            recovered[code] = ratio
    if not recovered:
        return {}

    ratios.update(recovered)
    peers = {market: sorted(ratio for code, ratio in ratios.items() if markets[code] == market)
             for market in ('KOSPI', 'KOSDAQ')}
    return {
        code: {'points': round(round(4 * _midrank(value, peers[markets[code]], 1), 6) * 3 / 4, 6),
               'available': 3, 'asof': day}
        for code, value in recovered.items() if len(peers[markets[code]]) >= 30
    }
