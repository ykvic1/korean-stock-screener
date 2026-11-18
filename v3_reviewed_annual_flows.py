"""Score reviewed full fiscal years for a receipt-linked short-period filing."""
from datetime import date, timedelta
import math

from v3_dart_bulk_history import _best_income_window, _midrank, research_parts
from v3_dart_cashflow_factor_preview import real_features
from v3_eps_valuation import evaluate


# The 2025 September report supplies the three comparative statements. The
# 2023 September report confirms the FY2022 base and the FY2023 intangible roll-forward.
REVIEWED_WINDOWS = {
    '154030': {
        'stub_receipt_id': '20260320000001',
        'scope': 'CFS',
        'source_filed_on': '2025-12-19',
        'source_url': 'https://kind.krx.co.kr/external/2025/12/19/000919/20251219002069/11011.htm',
        'supporting_source_url': 'https://kind.krx.co.kr/external/2023/12/21/000542/20231221001361/11011.htm',
        'periods': (
            {'start': '2021-10-01', 'end': '2022-09-30',
             'revenue': 24047390439, 'operating_income': -1595951716},
            {'start': '2022-10-01', 'end': '2023-09-30',
             'revenue': 26395658878, 'operating_income': -545738261,
             'eps': -204, 'assets': 42436154164, 'cfo': -258359661,
             'ppe_cash': 201719309, 'intangible_cash': 0,
             'other_investing_cash': -379725261, 'investing_cash': -581444570},
            {'start': '2023-10-01', 'end': '2024-09-30',
             'revenue': 24593549936, 'operating_income': -1124665061,
             'eps': -53, 'assets': 41075803079, 'cfo': 753674860,
             'ppe_cash': 130476973, 'intangible_cash': 0,
             'other_investing_cash': 723739392, 'investing_cash': 593262419},
            {'start': '2024-10-01', 'end': '2025-09-30',
             'revenue': 27806411703, 'operating_income': 923849151,
             'eps': 158, 'assets': 42242032938, 'cfo': 1005822201,
             'ppe_cash': 40177610, 'intangible_cash': 0, 'owners_profit': 1905122329,
             'other_investing_cash': -1056393437, 'investing_cash': -1096571047},
        ),
    },
}

FCF_ASSETS = 'financial_fcf_assets_3y_pct'
FCF_YIELD = 'valuation_fcf_yield_pct'
GROWTH = 'revenue_cagr_3y_pct'
MARGIN = 'operating_margin_3y_pct'
SALES = 'sales_to_market_cap'
EARNINGS = 'earnings_yield_pct'
MULTI_PERIOD_METRICS = (GROWTH, MARGIN, FCF_ASSETS)


def window_metrics(window, stub, fiscal_years, day, capitalization):
    """Verify duration, continuity, scope and source identity before ranking."""
    if (window['scope'] != 'CFS' or window['source_filed_on'] > day
            or window['stub_receipt_id'] != stub['receipt_id']
            or not stub['latest_annual_period']):
        return {}
    periods = window['periods']
    starts = [date.fromisoformat(p['start']) for p in periods]
    ends = [date.fromisoformat(p['end']) for p in periods]
    if (len(periods) != 4 or [end.year for end in ends] != fiscal_years
            or any(not 365 <= (end - start).days + 1 <= 366 for start, end in zip(starts, ends))
            or any(end + timedelta(days=1) != start for end, start in zip(ends, starts[1:]))
            or ends[-1] + timedelta(days=1) != date.fromisoformat(stub['period_start'])
            or ends[-1].isoformat() >= window['source_filed_on']):
        return {}
    metrics = _best_income_window({'years': {
        str(end.year): {window['scope']: {'values': period}}
        for end, period in zip(ends, periods)}}, fiscal_years)
    if metrics is None:
        return {}

    fcf = []
    for period in periods[-3:]:
        ppe, intangible = period['ppe_cash'], period['intangible_cash']
        if (ppe < 0 or intangible < 0 or period['assets'] <= 0
                or period['other_investing_cash'] - ppe - intangible != period['investing_cash']):
            break
        fcf.append(period['cfo'] - ppe - intangible)
    if len(fcf) == 3:
        metrics[FCF_ASSETS] = round(sum(
            100 * val / period['assets'] for val, period in zip(fcf, periods[-3:])) / 3, 6)

    latest = periods[-1]
    if type(capitalization) in (int, float) and math.isfinite(capitalization) and 0 < capitalization < 1e12:
        cap_won = capitalization * 1e8
        metrics[SALES] = round(latest['revenue'] / cap_won, 8)
        metrics[EARNINGS] = round(100 * latest['owners_profit'] / cap_won, 6)
        if len(fcf) == 3:
            metrics[FCF_YIELD] = round(100 * fcf[-1] / cap_won, 6)
    return metrics


def full_year_parts(catalog, history, book, cash, short_periods):
    day = catalog['date']
    if not book or history.get('asof') != day or book.get('asof') != day:
        return {}
    members = {m['code']: m for m in catalog['rows']}
    windows = {code: window_metrics(window, short_periods[code], history['fiscal_years'], day,
                                   members[code].get('reference_market_cap_eok'))
               for code, window in REVIEWED_WINDOWS.items() if code in short_periods and code in members}
    windows = {code: metrics for code, metrics in windows.items() if metrics}
    if not windows:
        return {}

    baseline = research_parts(history, catalog, book, include_independent_earnings=True)['rows']
    observed = {row['code']: {metric: part['observed_value']
                              for metric, part in row['unverified_components'].items()}
                for row in baseline}
    if cash and cash.get('asof') == day:
        for code, fcf in real_features(catalog, history, cash).items():
            observed.setdefault(code, {}).update(fcf)
    else:
        for metrics in windows.values():
            metrics.pop(FCF_ASSETS, None)
            metrics.pop(FCF_YIELD, None)
    for code, short in short_periods.items():
        row = observed.get(code, {})
        for metric in MULTI_PERIOD_METRICS:
            row.pop(metric, None)
        if short['latest_annual_period']:
            for metric in (SALES, EARNINGS, FCF_YIELD):
                row.pop(metric, None)
    for code, metrics in windows.items():
        observed.setdefault(code, {}).update(metrics)

    mapping = (
        ('financial', 'revenue_growth', GROWTH, 4, 3.5),
        ('financial', 'operating_growth_margin', MARGIN, 4, 3),
        ('financial', 'cashflow_fcf_persistence', FCF_ASSETS, 4, 3),
        ('valuation', 'sales_to_cap', SALES, 5, 2.5),
        ('valuation', 'fcf_yield', FCF_YIELD, 5, 2.5),
        ('valuation', 'per_eps_quality_growth', EARNINGS, 5, 5/3),
    )
    result = {}
    for code, metrics in windows.items():
        market = members[code]['market']
        parts = {'financial': {}, 'valuation': {}}
        for area, key, metric, base_weight, weight in mapping:
            if metric not in metrics:
                continue
            peers = sorted(row[metric] for peer, row in observed.items() if peer in members
                           and members[peer]['market'] == market and row.get(metric) is not None)
            if len(peers) >= 30:
                earned = round(base_weight * _midrank(metrics[metric], peers, 1), 6)
                parts[area][key] = {'points': round(earned * weight / base_weight, 6),
                                    'available': round(weight, 6), 'asof': day}
        original = REVIEWED_WINDOWS[code]
        eps = [p['eps'] for p in original['periods'][-3:]]
        vparts, _ = evaluate(eps, dict.fromkeys(('pbr', EARNINGS, 'fcf_yield', SALES), 0))
        part = parts['valuation'].get('per_eps_quality_growth')
        parts['valuation']['per_eps_quality_growth'] = {
            'points': round((vparts['eps_growth'] + vparts['eps_stability']) * 5/12
                            + (part['points'] if part else 0), 6),
            'available': 5 if part else round(10/3, 6), 'asof': day}
        result[code] = (parts, {k: original[k] for k in ('scope', 'source_filed_on', 'source_url',
                                                       'supporting_source_url')}
                              | {'period_end': original['periods'][-1]['end']})
    return result
