"""Apply reviewed DART investing cash-flow subtotals to receipt-matched FCF."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

from v3_dart_bulk_history import _valid_receipt

PROOF = 'reviewed_disaggregated_capex'
PARTS = ('ppe_capex_krw', 'intangible_capex_krw')
REGISTRY = Path(__file__).with_name('v3_dart_reviewed_capex_statements.json')


@lru_cache(maxsize=1)
def statements():
    data = json.loads(REGISTRY.read_bytes())
    if data['schema'] != 'v3-dart-reviewed-disaggregated-capex-v1':
        raise ValueError('invalid_reviewed_capex_registry')
    return data['records']


def reviewed_year(code, year, years, cutoff):
    source = statements()[code]
    item = source['years'][year]
    row = years[year]
    latest = years['2025']
    receipt = source['source_receipt']
    if (source['scope'] not in ('OFS', 'CFS')
            or not _valid_receipt(receipt, 2025, cutoff)
            or not _valid_receipt(item['annual_receipt'], int(year), cutoff)
            or not re.fullmatch(r'[a-f0-9]{64}', source['source_sha256'])
            or not source['source_url'].startswith(
                f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&')
            or row['scope'] != source['scope'] or latest['scope'] != source['scope']
            or row['major_annual_receipt_matched'] is not True
            or latest['major_annual_receipt_matched'] is not True
            or latest['annual_receipt_id'] != receipt
            or row['annual_receipt_id'] != item['annual_receipt']
            or type(row['operating_cashflow_krw']) is not int
            or row['operating_cashflow_krw'] != item['operating_cashflow_krw']
            or latest['operating_cashflow_krw'] != source['years']['2025']['operating_cashflow_krw']
            or any(type(item[k]) is not int for k in (*PARTS, 'investing_cashflow_krw',
                                                      'other_investing_cashflow_krw'))
            or any(item[k] < 0 for k in PARTS)
            or item['other_investing_cashflow_krw'] - sum(item[k] for k in PARTS)
                != item['investing_cashflow_krw']):
        raise ValueError('unverified_reviewed_capex_statement')
    return item, {'source_receipt': receipt, 'source_sha256': source['source_sha256']}


def verify(code, year, years, cutoff):
    item, proof = reviewed_year(code, year, years, cutoff)
    row = years[year]
    if row[PROOF] != proof or any(row[k] != item[k] for k in PARTS):
        raise ValueError('reviewed_capex_amount_or_proof_mismatch')


def restore(row):
    return {**{k: v for k, v in row.items() if k != PROOF},
            **dict.fromkeys((*PARTS, 'total_capex_krw', 'free_cashflow_krw'))}


def apply(cash):
    from datetime import date
    cutoff = date.fromisoformat(cash['asof'])
    proposed = deepcopy(cash)
    added = []
    for code, source in statements().items():
        years = proposed['records'].get(code, {}).get('years', {})
        if '2025' not in years or years['2025']['annual_receipt_id'] != source['source_receipt']:
            continue
        for year, expected in source['years'].items():
            row = years.get(year)
            if (not row or row['annual_receipt_id'] != expected['annual_receipt']
                    or row['free_cashflow_krw'] is not None
                    or any(row[k] is not None for k in PARTS)):
                continue
            item, proof = reviewed_year(code, year, years, cutoff)
            capex = sum(item[k] for k in PARTS)
            row.update({k: item[k] for k in PARTS}, total_capex_krw=capex,
                       free_cashflow_krw=row['operating_cashflow_krw']-capex)
            row[PROOF] = proof
            added.append((code, year))
    return proposed, added
