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


def validate_statement(item, row, year, cutoff, *, scope, receipt, source_year, source):
    if (scope not in ('OFS', 'CFS')
            or not _valid_receipt(receipt, source_year, cutoff)
            or not _valid_receipt(item['annual_receipt'], int(year), cutoff)
            or not re.fullmatch(r'[a-f0-9]{64}', source['source_sha256'])
            or not source['source_url'].startswith(
                f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&')
            or row['scope'] != scope or row['major_annual_receipt_matched'] is not True
            or row['annual_receipt_id'] != item['annual_receipt']
            or type(row['operating_cashflow_krw']) is not int
            or row['operating_cashflow_krw'] != item['operating_cashflow_krw']
            or any(type(item[k]) is not int for k in (*PARTS, 'investing_cashflow_krw',
                                                      'other_investing_cashflow_krw'))
            or any(not 0 <= item[k] <= 10**18 for k in PARTS)
            or item['other_investing_cashflow_krw'] - sum(item[k] for k in PARTS)
                != item['investing_cashflow_krw']):
        raise ValueError('unverified_reviewed_capex_statement')
    return {'source_receipt': receipt, 'source_sha256': source['source_sha256']}


def reviewed_year(code, year, years, cutoff):
    source = statements()[code]
    item = source['years'][year]
    row = years[year]
    latest = years['2025']
    receipt = source['source_receipt']
    if (latest['scope'] != source['scope']
            or latest['major_annual_receipt_matched'] is not True
            or latest['annual_receipt_id'] != receipt
            or latest['operating_cashflow_krw'] != source['years']['2025']['operating_cashflow_krw']):
        raise ValueError('unverified_reviewed_capex_statement')
    return item, validate_statement(item, row, year, cutoff, scope=source['scope'],
                                    receipt=receipt, source_year=2025, source=source)


def original_year(item, row, year, cutoff):
    proof = validate_statement(item, row, year, cutoff, scope=item['scope'],
                               receipt=item['annual_receipt'], source_year=int(year), source=item)
    return {**proof, 'source_fiscal_year': int(year)}


def verify(code, year, years, cutoff):
    row = years[year]
    if 'source_fiscal_year' in row[PROOF]:
        item = statements()[code]['original_statements'][year]
        proof = original_year(item, row, year, cutoff)
    else:
        item, proof = reviewed_year(code, year, years, cutoff)
    if row[PROOF] != proof or any(row[k] != item[k] for k in PARTS):
        raise ValueError('reviewed_capex_amount_or_proof_mismatch')


def restore(row):
    return {**{k: v for k, v in row.items() if k != PROOF},
            **dict.fromkeys((*PARTS, 'total_capex_krw', 'free_cashflow_krw'))}


def apply_statement(row, item, proof):
    capex = sum(item[k] for k in PARTS)
    row.update({k: item[k] for k in PARTS}, total_capex_krw=capex,
               free_cashflow_krw=row['operating_cashflow_krw']-capex)
    row[PROOF] = proof


def apply(cash):
    from datetime import date
    cutoff = date.fromisoformat(cash['asof'])
    proposed = deepcopy(cash)
    added = []
    for code, source in statements().items():
        years = proposed['records'].get(code, {}).get('years', {})
        for year, item in source.get('original_statements', {}).items():
            row = years.get(year)
            if (not row or row['annual_receipt_id'] != item['annual_receipt']
                    or row['free_cashflow_krw'] is not None
                    or all(row[k] is not None for k in PARTS)
                    or any(row[k] is not None and row[k] != item[k] for k in PARTS)):
                continue
            proof = original_year(item, row, year, cutoff)
            apply_statement(row, item, proof)
            added.append((code, year))
        if not source.get('years') or '2025' not in years or years['2025']['annual_receipt_id'] != source['source_receipt']:
            continue
        for year, expected in source['years'].items():
            row = years.get(year)
            if (not row or row['annual_receipt_id'] != expected['annual_receipt']
                    or row['free_cashflow_krw'] is not None
                    or any(row[k] is not None for k in PARTS)):
                continue
            item, proof = reviewed_year(code, year, years, cutoff)
            apply_statement(row, item, proof)
            added.append((code, year))
    return proposed, added
