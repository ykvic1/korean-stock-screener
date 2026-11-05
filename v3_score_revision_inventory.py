"""Offline inventory for a PROPOSED model; never publishes issuer scores."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def inventory(history):
    years = history['fiscal_years']
    if (not isinstance(years, list) or len(years) < 3 or
        any(type(y) is not int for y in years) or years != sorted(set(years))):
        raise ValueError('invalid_revision_inventory_years')
    observations = {key: [] for key in (
        'equity_assets_pct', 'liabilities_assets_pct', 'operating_margin_pct',
        'annual_loss_fraction')}
    for record in history['records'].values():
        annual = record.get('years', {})
        scope = next((s for s in ('CFS', 'OFS') if all(
            isinstance(annual.get(str(y), {}).get(s, {}).get('values'), dict)
            for y in years)), None)
        if scope is None:
            continue
        latest = annual[str(years[-1])][scope]['values']
        for key, numerator, denominator in (
            ('equity_assets_pct', 'equity', 'assets'),
            ('liabilities_assets_pct', 'liabilities', 'assets'),
            ('operating_margin_pct', 'operating_income', 'revenue')):
            n, d = latest.get(numerator), latest.get(denominator)
            if numeric(n) and numeric(d) and d > 0:
                observations[key].append(100 * n / d)
        profits = [annual[str(y)][scope]['values'].get('operating_income') for y in years]
        if all(numeric(p) for p in profits):
            observations['annual_loss_fraction'].append(sum(p < 0 for p in profits) / len(years))
    result = {}
    for key, values in observations.items():
        values.sort()
        result[key] = {
            'observed': len(values),
            'unobserved': len(history['records']) - len(values),
            'nonpositive': sum(v <= 0 for v in values),
            'quantiles_nearest_index': {
                str(q): round(values[round((len(values)-1) * q)], 4) if values else None
                for q in (.05, .25, .5, .75, .95)}}
    return {'status': 'proposal_inventory_only', 'source_asof': history['asof'],
            'fiscal_years': years, 'listed_records': len(history['records']),
            'scope_rule': 'same_scope_all_years_CFS_else_OFS', 'distributions': result,
            'provider_calls': 0, 'issuer_scores_written': 0, 'historical_pit_certified': False,
            'sector_neutral_thresholds_validated': False, 'production_model_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', type=Path, default=Path('docs/v3-dart-bulk-history.json'))
    parser.add_argument('--proposal', type=Path, default=Path('config/v3-score-revision-r1.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    proposal = json.loads(args.proposal.read_text())
    totals = {area: sum(parts.values()) for area, parts in proposal['weights'].items()}
    if totals != {'volume': 25, 'technical': 30, 'financial': 12.5, 'valuation': 12.5, 'momentum': 20}:
        raise ValueError('proposal_subweights_do_not_match_100_point_budget')
    if sum(totals.values()) != 100 or proposal['status'] != 'proposed_not_implemented':
        raise ValueError('untrusted_revision_proposal')
    if (proposal['weights']['volume']['obv_price_divergence'] > 5 or
        proposal['weights']['technical']['daily_pattern'] +
        proposal['weights']['technical']['weekly_pattern'] != 18):
        raise ValueError('proposal_obv_or_pattern_contract_changed')
    raw = args.history.read_bytes()
    result = inventory(json.loads(raw))
    result.update({'model_proposal': proposal['version'], 'area_totals': totals,
                   'total_points': sum(totals.values()), 'source_sha256': sha256(raw).hexdigest(),
                   'proposal_sha256': sha256(args.proposal.read_bytes()).hexdigest()})
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'total_points': result['total_points'],
                      'provider_calls': 0, 'production_model_changed': False}))


if __name__ == '__main__':
    main()
