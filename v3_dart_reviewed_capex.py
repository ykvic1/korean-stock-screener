"""Apply reviewed DART investing cash-flow subtotals to receipt-matched FCF."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

from v3_dart_bulk_history import _valid_receipt, matches_dart_viewer

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
            or not matches_dart_viewer(source['source_url'], receipt)
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
    if 'superseded_partial_capex' in item:
        partial = item['superseded_partial_capex']
        if (type(partial) is not dict or len(partial) != 1
                or not partial.keys() <= set(PARTS)
                or any(type(v) is not int or not 0 <= v < item[k] for k, v in partial.items())):
            raise ValueError('unverified_reviewed_capex_partial_replacement')
    if any(k in item for k in ('superseded_partial_capex', 'supporting_notes_url', 'supporting_notes_sha256')):
        if (not matches_dart_viewer(item.get('supporting_notes_url', ''), item['annual_receipt'])
                or item['supporting_notes_url'] == item['source_url']
                or not re.fullmatch(r'[a-f0-9]{64}', item.get('supporting_notes_sha256', ''))
                or item['supporting_notes_sha256'] == item['source_sha256']):
            raise ValueError('unverified_reviewed_capex_partial_replacement')
        proof['supporting_notes_sha256'] = item['supporting_notes_sha256']
    return {**proof, 'source_fiscal_year': int(year)}


# A later filing can fill exactly one missing component only when the
# independently retained component agrees exactly and has no competing proof.
# Keep the original parts in the provenance so a revised later receipt cannot
# erase the valid original-year observation.
PROTECTED_PARTIAL_FIELDS = (
    'source_account_name_fallback', 'original_xbrl_proof', 'original_xbrl_review',
    'full_intangible_total', 'reported_total_review', 'reported_capex_total',
    'later_original_comparative_proof', PROOF,
)


def has_competing_partial_proof(row):
    """A verified no-result review is not a competing numeric observation."""
    for key in PROTECTED_PARTIAL_FIELDS:
        if row.get(key) is None:
            continue
        if key == 'original_xbrl_review':
            from v3_dart_xbrl_cashflow_recovery import verify_recovery
            marker = row[key]
            if (type(marker) is dict and marker.get('reason') == 'no_missing_supported_fact'
                    and verify_recovery(row) is False):
                continue
        if key == 'reported_total_review':
            from v3_dart_reported_capex_total import verify
            marker = row[key]
            if (type(marker) is dict and marker.get('reason') == 'no_reported_total'
                    and verify(row) is None):
                continue
        return True
    return False


def matching_partial(parts, item):
    return (type(parts) is dict and set(parts) == set(PARTS)
            and sum(parts[k] is None for k in PARTS) == 1
            and all(parts[k] is None or
                    (type(parts[k]) is int and parts[k] >= 0 and parts[k] == item[k])
                    for k in PARTS))


def verify(code, year, years, cutoff):
    row = years[year]
    if 'source_fiscal_year' in row[PROOF]:
        item = statements()[code]['original_statements'][year]
        proof = original_year(item, row, year, cutoff)
    else:
        item, proof = reviewed_year(code, year, years, cutoff)
        if 'existing_parts' in row[PROOF]:
            existing = row[PROOF]['existing_parts']
            if not matching_partial(existing, item):
                raise ValueError('unverified_reviewed_capex_existing_parts')
            proof['existing_parts'] = existing
    # Earlier no-replacement observations stored only the statement digest.
    # The registry notes still undergo identity and digest validation above.
    compatible_proof = proof
    if 'superseded_partial_capex' not in item and 'supporting_notes_sha256' not in row[PROOF]:
        compatible_proof = {key: value for key, value in proof.items() if key != 'supporting_notes_sha256'}
    if row[PROOF] != compatible_proof or any(row[k] != item[k] for k in PARTS):
        raise ValueError('reviewed_capex_amount_or_proof_mismatch')


def restore(row):
    existing = row[PROOF].get('existing_parts', dict.fromkeys(PARTS))
    if (type(existing) is not dict or set(existing) != set(PARTS)
            or ('existing_parts' in row[PROOF] and not matching_partial(existing, row))):
        raise ValueError('unverified_reviewed_capex_existing_parts')
    return {**{k: v for k, v in row.items() if k != PROOF},
            **existing, 'total_capex_krw': None, 'free_cashflow_krw': None}


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
                    or all(row[k] is not None for k in PARTS)):
                continue
            replacements = {k: row[k] for k in PARTS if row[k] is not None and row[k] != item[k]}
            if replacements != item.get('superseded_partial_capex', {}):
                continue
            if replacements and any(row.get(k) is not None for k in (
                    'source_account_name_fallback', 'original_xbrl_proof', 'full_intangible_total',
                    'reported_capex_total', 'later_original_comparative_proof')):
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
                    or all(row[k] is not None for k in PARTS)):
                continue
            existing = {k: row[k] for k in PARTS}
            partial = any(v is not None for v in existing.values())
            if partial and (not matching_partial(existing, expected)
                            or has_competing_partial_proof(row)):
                continue
            item, proof = reviewed_year(code, year, years, cutoff)
            if partial:
                proof['existing_parts'] = existing
            apply_statement(row, item, proof)
            added.append((code, year))
    return proposed, added
