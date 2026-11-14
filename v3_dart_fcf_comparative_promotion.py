"""Safely promote independently CFO-anchored original CAPEX comparatives.

Only same-issuer/scope annual FY2024/FY2025 original XBRL facts whose prior-year
CFO equals the independently receipt-matched original-year CFO can supplement
missing older CAPEX. Every numeric component comes from an explicit standard
IFRS fact, never a missing-as-zero inference. This updates CURRENT observations
only; historical PIT remains unverified. Original-year receipts are preserved.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

import v3_dart_cashflow_enrichment as cf
import v3_dart_fcf_prior_comparative_probe as research
from v3_dart_bulk_history import write_json

CONTRACT = 'two_annual_receipts_same_scope_cfo_correlated_original_capex_v1'
FY2024_CONTRACT = 'two_annual_receipts_both_cfo_anchors_fy2024_capex_v1'
PROOF = 'later_original_comparative_proof'
PARTS = ('ppe_capex_krw', 'intangible_capex_krw')


class ComparativePromotionError(ValueError):
    pass


def verify_promoted_year(year_row, issuer_years, code, year):
    proof = year_row.get(PROOF)
    if type(proof) is not dict:
        raise ComparativePromotionError('invalid_cross_receipt_capex_proof')
    source_year = proof.get('source_fiscal_year', 2025)
    keys = {
        'contract', 'code', 'year', 'scope', 'prior_receipt', 'later_receipt',
        'cfo_anchor', 'archive_sha256', 'profile_sha256', 'source_record_digest',
        'existing_parts', 'added_parts', 'digest'}
    if proof.get('contract') == FY2024_CONTRACT:
        keys |= {'source_fiscal_year', 'source_cfo_anchor'}
        if type(source_year) is not int or source_year != 2024 or int(year) != 2023:
            raise ComparativePromotionError('invalid_fy2024_comparative_source')
    elif proof.get('contract') != CONTRACT or source_year != 2025:
        raise ComparativePromotionError('invalid_cross_receipt_capex_contract')
    if set(proof) != keys:
        raise ComparativePromotionError('invalid_cross_receipt_capex_proof')
    later = issuer_years.get(str(source_year), {})
    if (proof.get('contract') == FY2024_CONTRACT and
        (type(proof['source_cfo_anchor']) is not int or
         proof['source_cfo_anchor'] != later.get('operating_cashflow_krw'))):
        raise ComparativePromotionError('unverified_fy2024_source_cfo')
    if (proof['code'] != code or proof['year'] != int(year)
        or proof['scope'] != year_row.get('scope') or proof['scope'] != later.get('scope')
        or year_row.get('major_annual_receipt_matched') is not True
        or later.get('major_annual_receipt_matched') is not True
        or later.get('year') != source_year
        or proof['prior_receipt'] != year_row.get('annual_receipt_id')
        or proof['later_receipt'] != later.get('annual_receipt_id')
        or not re.fullmatch(f'{source_year+1}[0-9]{{10}}', str(proof['later_receipt']))
        or proof['cfo_anchor'] != year_row.get('operating_cashflow_krw')
        or type(proof['cfo_anchor']) is not int
        or type(proof['existing_parts']) is not dict or set(proof['existing_parts']) != set(PARTS)
        or type(proof['added_parts']) is not dict or not proof['added_parts']
        or not set(proof['added_parts']) <= set(PARTS)
        or any(proof['existing_parts'][k] is not None for k in proof['added_parts'])
        or any((type(v) is not int or v < 0) for v in proof['added_parts'].values())
        or any(v is not None and (type(v) is not int or v < 0)
               for v in proof['existing_parts'].values())
        or any(year_row.get(k) != (proof['added_parts'][k] if k in proof['added_parts']
                                   else proof['existing_parts'][k]) for k in PARTS)
        or not all(year_row.get(k) is not None for k in PARTS)
        or any(type(proof[k]) is not str or not re.fullmatch('[a-f0-9]{64}', proof[k])
               for k in ('archive_sha256', 'profile_sha256', 'source_record_digest'))
        or proof['digest'] != cf.fingerprint({k: v for k, v in proof.items() if k != 'digest'})):
        raise ComparativePromotionError('unverified_cross_receipt_capex_proof')
    return True


def restore_if_later_receipt_changed(year_row, *, actual_later_receipt):
    """A later corrected/withdrawn filing must revoke ONLY its added facts."""
    proof = year_row.get(PROOF)
    if proof is None or proof.get('later_receipt') == actual_later_receipt:
        return year_row
    out = deepcopy(year_row)
    for k, value in proof['existing_parts'].items():
        out[k] = value
    out.pop(PROOF)
    capex = out['ppe_capex_krw'] + out['intangible_capex_krw'] if all(
        out[k] is not None for k in PARTS) else None
    out['total_capex_krw'] = capex
    out['free_cashflow_krw'] = out['operating_cashflow_krw'] - capex if capex is not None else None
    return out


def promote_year(code, year, anchor, later, source_record, source_year, *, source_fiscal_year=2025):
    if (type(source_fiscal_year) is not int or source_fiscal_year not in (2024, 2025)
        or (source_fiscal_year == 2024 and (str(year) != '2023' or
            source_record.get('source_cfo_2024_krw') != later.get('operating_cashflow_krw')))):
        raise ComparativePromotionError('invalid_original_comparative_source_year')
    if anchor.get(PROOF) is not None:
        raise ComparativePromotionError('already_promoted_comparative_year')
    if (anchor.get('free_cashflow_krw') is not None
        or anchor.get('major_annual_receipt_matched') is not True
        or anchor.get('scope') != later.get('scope')
        or anchor.get('scope') != source_record.get('scope')
        or anchor.get('annual_receipt_id') != source_year.get('original_receipt')
        or later.get('annual_receipt_id') != source_record.get(f'fy{source_fiscal_year}_receipt')
        or anchor.get('operating_cashflow_krw') != source_year.get('anchor_cfo_krw')
        or type(anchor.get('operating_cashflow_krw')) is not int
        or source_year.get('reason') != 'complete_comparative_candidate'):
        raise ComparativePromotionError('unaligned_comparative_year')
    added = source_year['candidate_parts']
    if (not added or not set(added) <= set(PARTS)
        or any(anchor.get(k) is not None or type(v) is not int or v < 0
               for k, v in added.items())):
        raise ComparativePromotionError('invalid_original_added_component')
    out = deepcopy(anchor)
    existing = {k: anchor.get(k) for k in PARTS}
    out.update(added)
    if any(out[k] is None for k in PARTS):
        raise ComparativePromotionError('incomplete_comparative_capex_pair')
    out['total_capex_krw'] = out['ppe_capex_krw'] + out['intangible_capex_krw']
    out['free_cashflow_krw'] = out['operating_cashflow_krw'] - out['total_capex_krw']
    proof = {'contract': CONTRACT, 'code': code, 'year': int(year),
             'scope': anchor['scope'], 'prior_receipt': anchor['annual_receipt_id'],
             'later_receipt': later['annual_receipt_id'],
             'cfo_anchor': anchor['operating_cashflow_krw'],
             'archive_sha256': source_record['archive_sha256'],
             'profile_sha256': source_record['profile_sha256'],
             'source_record_digest': source_record['digest'],
             'existing_parts': existing, 'added_parts': dict(added)}
    if source_fiscal_year == 2024:
        proof.update(contract=FY2024_CONTRACT, source_fiscal_year=2024,
                     source_cfo_anchor=source_record['source_cfo_2024_krw'])
    proof['digest'] = cf.fingerprint(proof)
    out[PROOF] = proof
    verify_promoted_year(out, {str(year): out, str(source_fiscal_year): later}, code, year)
    return out


def candidates(cash, research_state):
    if (research_state.get('schema') != research.SCHEMA
        or research_state.get('scores_unchanged') is not True
        or type(research_state.get('records')) is not dict):
        raise ComparativePromotionError('untrusted_original_comparative_research')
    selected = []
    for code, source_record in sorted(research_state['records'].items()):
        research.verify_record(source_record)
        years = cash.get('records', {}).get(code, {}).get('years', {})
        if any(year not in years for year in ('2023', '2024', '2025')):
            continue
        later = years.get('2025', {})
        missing = research.eligible_missing_years(years, source_record['scope'])
        if (not missing or later.get('major_annual_receipt_matched') is not True
            or later.get('scope') != source_record['scope']
            or later.get('annual_receipt_id') != source_record['fy2025_receipt']
            or not research.anchors_unchanged(source_record, years)):
            continue
        complete = [(year, anchor) for year, anchor in missing
                    if source_record['years'][year]['reason'] == 'complete_comparative_candidate']
        if complete:
            selected.append((code, source_record, later, complete))
    return selected


def apply(cash, research_state, *, max_issuers=150):
    if type(max_issuers) is not int or not 1 <= max_issuers <= 150:
        raise ComparativePromotionError('invalid_comparative_promotion_limit')
    chosen = candidates(cash, research_state)[:max_issuers]
    output = deepcopy(cash)
    promoted = []
    for code, source_record, later, missing in chosen:
        row = output['records'][code]
        for year, anchor in missing:
            row['years'][year] = promote_year(code, year, anchor, later,
                source_record, source_record['years'][year])
        promoted.append(code)
    return output, promoted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('dry-run', 'promote'), default='dry-run')
    args = parser.parse_args()
    root = Path('docs')
    read = lambda name: json.loads((root / name).read_text())
    market, catalog, bulk, saved, source = [read(name) for name in (
        'data.json', 'v22-market-catalog.json', 'v3-dart-bulk-history.json',
        'v3-dart-cashflow-reference.json', 'v3-fcf-prior-comparative-research.json')]
    cash = cf.retained_current_market_cashflow(market, catalog, bulk, saved)
    proposed, codes = apply(cash, source)
    from v3_dart_fcf_2024_prior_probe import OUTPUT as FY2024_OUTPUT, apply as apply_fy2024
    if (root / FY2024_OUTPUT).is_file():
        proposed, additional = apply_fy2024(proposed, read(FY2024_OUTPUT))
        codes = sorted(set(codes) | set(additional))
    from v3_dart_reviewed_capex import apply as apply_reviewed
    proposed, reviewed = apply_reviewed(proposed)
    codes = sorted(set(codes) | {code for code, _ in reviewed})
    result = {'eligible_prior_year_issuer_repairs': len(codes),
              'reviewed_disaggregated_capex_years': len(reviewed),
              'new_three_year_fcf_issuer_completions': sum(
                  all(proposed['records'][code]['years'].get(year, {}).get('free_cashflow_krw') is not None
                      for year in ('2023', '2024', '2025'))
                  and not all(cash['records'][code]['years'].get(year, {}).get('free_cashflow_krw') is not None
                              for year in ('2023', '2024', '2025')) for code in codes),
              'added_years': sum(
                  old.get(PROOF) is None and proposed['records'][code]['years'][year].get(PROOF) is not None
                  for code in codes
                  for year,old in cash['records'][code]['years'].items()),
              'scores_unchanged_until_replay': True,
              'historical_pit_certified': False}
    if args.mode == 'promote' and codes:
        target = root / 'v3-dart-cashflow-reference.json'
        if target.is_symlink():
            raise ComparativePromotionError('unsafe_cashflow_promotion_output')
        day, members = cf.asof_members(market, catalog, bulk)
        updated = cf.summary(day, members, proposed['records'], cf.fingerprint(bulk))
        cf.verify_state(updated, market, catalog, bulk)
        write_json(target, updated)
        result['scores_unchanged_until_replay'] = True
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'blocked_comparative_promotion',
                          'reason': 'cross_receipt_proof_unverified'}))
        raise SystemExit(1)
