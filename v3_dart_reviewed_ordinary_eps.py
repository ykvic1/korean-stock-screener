"""Admit basic ordinary-share EPS transcribed from one original DART filing."""
from datetime import date
import re

from v3_dart_bulk_history import _valid_receipt
from v3_dart_reported_eps import metrics

OUTPUT = 'v3-dart-reviewed-ordinary-eps.json'
SCHEMA = 'v3-dart-reviewed-ordinary-eps-v1'
LABEL = re.compile(
    r'(?:지배기업소유주)?(?:보통주)?기본(?:(?:및|/)희석)?(?:보통)?주당(?:순)?'
    r'(?:이익(?:\(손실\))?|손익|순손실)(?:[,，]?보통주)?'
)
WON_PER_SHARE = re.compile(r'\(단위:원(?:/주)?\)')


def ordinary_basic_label(raw):
    if not isinstance(raw, str):
        return False
    label = re.sub(r'\s+', '', raw)
    if not WON_PER_SHARE.search(label):
        return False
    label = WON_PER_SHARE.sub('', label)
    label = re.sub(r'^\(\d+\)|^\d+\.|\(주\d+\)', '', label)
    return LABEL.fullmatch(label) is not None


def reviewed_eps_parts(retained, history, cash, reviewed, day):
    if reviewed is None or cash is None:
        return {}
    if reviewed.get('schema') != SCHEMA or not isinstance(reviewed.get('records'), dict):
        raise ValueError('invalid_reviewed_ordinary_eps_registry')
    cutoff = date.fromisoformat(day)
    result = {}
    for code, evidence in reviewed['records'].items():
        baseline = retained.get(code, {})
        if (baseline.get('status') not in ('reported_basic_eps', 'standard_eps_not_observed')
                or baseline['metrics']['complete_three_years']):
            continue
        scope = evidence['scope']
        receipt = evidence['receipt_id']
        if (scope not in ('CFS', 'OFS') or baseline['fs_div'] != scope
                or baseline['annual_receipt_id'] != receipt
                or not _valid_receipt(receipt, 2025, cutoff)
                or not ordinary_basic_label(evidence['statement_label'])):
            continue
        annual = history.get('records', {}).get(code, {}).get('years', {}).get('2025', {}).get(scope, {})
        cfo = cash.get('records', {}).get(code, {}).get('years', {}).get('2025', {})
        if (annual.get('receipt_id') != receipt or cfo.get('annual_receipt_id') != receipt
                or cfo.get('scope') != scope or cfo.get('major_annual_receipt_matched') is not True
                or type(evidence['operating_cashflow_krw']) is not int
                or cfo.get('operating_cashflow_krw') != evidence['operating_cashflow_krw']):
            continue
        kind = evidence.get('kind_annual_statement')
        if kind is None:
            prefix = f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&'
            statements = [evidence[key] for key in ('income_statement', 'cashflow_statement')]
            if (any(not entry['url'].startswith(prefix)
                    or not re.fullmatch('[a-f0-9]{64}', entry['sha256']) for entry in statements)
                    or statements[0]['url'] == statements[1]['url']
                    or statements[0]['sha256'] == statements[1]['sha256']):
                continue
        else:
            filing_day = f'{receipt[:4]}/{receipt[4:6]}/{receipt[6:8]}'
            kind_prefix = f'https://kind.krx.co.kr/external/{filing_day}/'
            if not isinstance(kind, dict):
                continue
            kind_url = kind.get('url', '')
            values = annual.get('values', {})
            if (not isinstance(kind_url, str) or not kind_url.startswith(kind_prefix)
                    or not re.fullmatch(r'\d{6}/' + receipt[:8] + r'\d{6}/11011\.htm',
                                        kind_url[len(kind_prefix):])
                    or any(type(kind.get(field)) is not int or values.get(field) != kind[field]
                           or field in annual.get('ambiguous', ())
                           for field in ('revenue', 'operating_income'))):
                continue
        series = evidence['basic_eps']
        try:
            summary = metrics(series)
        except (TypeError, ValueError):
            continue
        if (not summary['complete_three_years']
                or any(known is not None and known != added for known, added in
                       zip(baseline['reported_basic_eps_2023_2025'], series))):
            continue
        result[code] = {**baseline, 'status': 'reported_basic_eps',
                        'comparative_basis': ('reviewed_kind_annual_filing_ordinary_share_eps'
                                              if kind is not None else
                                              'reviewed_same_annual_filing_ordinary_share_eps'),
                        'reported_basic_eps_2023_2025': series, 'metrics': summary}
    return result
