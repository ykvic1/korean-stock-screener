"""Read-only acceptance evidence for the published operational model.

Reconcile the CSV with every JSON field, account for omitted price rows, and
exercise the same JavaScript attachment used by Pages. This does not certify
source prices, session scope, historical scores or investment performance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess

from v3_operational_research_score import to_csv
from v3_v30b_release_gate import r1d_public_reconciles

ROOT = Path(__file__).resolve().parent
FILES = ('data.json', 'v22-market-catalog.json',
         'v3-operational-research-scores.json', 'v3-operational-research-scores.csv',
         'v3-operational-research-comparison.json', 'v3-trading-status.json')
BANDS = ('100', '95_to_under_100', '90_to_under_95', '80_to_under_90',
         '70_to_under_80', '60_to_under_70', 'under_60')
FAILED_REFRESH = {
    'workflow_run_id': 43487515513,
    'failed_job_id': 132099769468,
    'failed_at_utc': '2026-11-13T18:57:09+00:00',
    'failed_step': 'Recompute from saved ledgers without provider calls or collection locks',
    'failure_code': 'unaligned_operating_comparative_sources',
    'before_commit': '23dbdc365fb86d5165562bb86526f797ebf068cc',
    'after_commit': '45c4f46c1f792ffaab972b29ce67e1123be1d90c',
}


def failed_refresh_evidence(root: Path, incident: dict) -> dict:
    """Recheck the main commits bracketing a reviewed production failure."""
    paths = tuple('docs/' + name for name in (*FILES, 'index.html', 'workspace.html'))
    before, after = incident['before_commit'], incident['after_commit']

    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                              text=True, encoding='utf-8').stdout

    def blobs(commit):
        records = git('ls-tree', '-z', commit, '--', *paths).split('\0')
        return {path: meta.split()[2] for meta, path in
                (row.split('\t', 1) for row in records if row) if meta.split()[1] == 'blob'}

    try:
        parents = git('show', '-s', '--format=%P', after).split()
        before_time = int(git('show', '-s', '--format=%ct', before))
        after_time = int(git('show', '-s', '--format=%ct', after))
        failed_at = datetime.fromisoformat(incident['failed_at_utc']).timestamp()
        earlier, later = blobs(before), blobs(after)
    except (OSError, subprocess.CalledProcessError):
        return {**incident, 'status': 'git_history_unavailable'}

    matches = {path: earlier[path] for path in paths
               if path in earlier and earlier[path] == later.get(path)}
    checks = {
        'after_is_direct_child_of_before': parents == [before],
        'failure_time_between_commits': before_time < failed_at < after_time,
        'all_expected_publication_blobs_matched': len(matches) == len(paths),
    }
    return {
        **incident, 'status': 'verified_preserved' if all(checks.values()) else 'evidence_mismatch',
        'scope': 'saved_source_recompute_failure_before_publication',
        'checks': checks, 'matched_file_count': len(matches), 'expected_file_count': len(paths),
        'matched_git_blob_ids': matches,
    }


def audit(docs: Path, *, failed_refresh: dict | None = None) -> dict:
    raw = {name: (docs / name).read_bytes() for name in FILES}
    loaded = {name: json.loads(value) for name, value in raw.items() if name.endswith('.json')}
    market, catalog, report, comparison, trading = (loaded[name] for name in
        (FILES[0], FILES[1], FILES[2], FILES[4], FILES[5]))
    checks = {}
    checks['score_parts_totals_ranks_and_roster'] = r1d_public_reconciles(
        report, comparison, catalog=catalog) is not None
    day = report['asof']
    checks['same_market_day'] = market.get('date') == catalog.get('date') == day
    expected = list(csv.reader(io.StringIO(to_csv(report))))
    actual = list(csv.reader(io.StringIO(raw[FILES[3]].decode('utf-8-sig'))))
    checks['every_operational_csv_cell'] = actual == expected

    identities = {row['code']: (row['name'], row['market']) for row in catalog['rows']}
    prices = market['stocks']
    codes = [row['code'] for row in prices]
    checks['priced_issuer_identity'] = (len(set(codes)) == len(codes) and
        all(identities.get(row['code']) == (row['name'], row['market']) and
            row.get('price_date') == day for row in prices))
    missing = set(identities) - set(codes)
    errors, exclusions = market['collection_errors'], market['history_exclusions']
    # Keep provider failures distinct from zero-volume observations. A flow
    # error may coexist with a valid price row and must not hide that issuer.
    checks['collection_reason_schema'] = (
        isinstance(errors, list) and isinstance(exclusions, list) and
        all(isinstance(x, list) and len(x) == 3 and x[0] in identities and
            all(type(v) is str for v in x) for x in errors) and
        all(isinstance(x, list) and len(x) == 2 and x[0] in identities and
            all(type(v) is str for v in x) for x in exclusions) and
        type(market.get('error_count')) is int and market['error_count'] == len(errors))
    reason_codes = {x[0] for x in errors + exclusions}
    checks['all_unpriced_issuers_explained'] = not (missing - reason_codes)
    checks['examined_entire_roster'] = (market.get('eligible_count') ==
        market.get('examined_count') == catalog['listed_total'] == report['listed_total'])
    zero_codes = {row['code'] for row in catalog['rows']
                  if row.get('listing_zero_volume_reference') is True}
    status_rows = trading.get('rows', {})
    checks['zero_volume_display_retained'] = (
        trading.get('asof') == day and trading.get('listed_total') == len(identities) and
        trading.get('counts', {}).get('zero_volume') == len(zero_codes) and
        all(status_rows.get(code, {}).get('state') == 'zero_volume' and
            (status_rows[code].get('name'), status_rows[code].get('market')) == identities[code]
            for code in zero_codes) and
        {code for code, row in status_rows.items() if row.get('state') == 'zero_volume'} == zero_codes)
    node = subprocess.run(['node', str(ROOT / 'scripts/audit_operational_publication.cjs'),
                           str(docs.resolve())], capture_output=True, text=True, check=False)
    if node.returncode:
        checks['pages_attachment_and_details'] = False
        pages = {'status': 'failed'}
    else:
        pages = json.loads(node.stdout)
        checks['pages_attachment_and_details'] = pages.get('status') == 'coherent'
    distribution = dict.fromkeys(BANDS, 0)
    for row in report['rows'].values():
        index = next((i for i, bound in enumerate((100, 95, 90, 80, 70, 60))
                      if row['available'] >= bound), 6)
        distribution[BANDS[index]] += 1
    remaining = ['KRX_regular_only_volume_turnover_and_OBV_source_scope']
    if failed_refresh is None or failed_refresh['status'] != 'verified_preserved':
        remaining.append('actual_failed_refresh_preserves_previous_publication')
    return {
        'schema': 'v3-operational-publication-acceptance-1', 'asof': day,
        'publication_coherent': all(checks.values()), 'checks': checks,
        'listed_total': report['listed_total'], 'priced_count': len(prices),
        'unpriced_count': len(missing), 'unexplained_unpriced_count': len(missing-reason_codes),
        'available_weight_distribution': distribution,
        'zero_volume_reference_count': len(zero_codes),
        'unpriced_zero_volume_count': len(missing & zero_codes),
        'unpriced_history_exclusion_count': len(missing & {x[0] for x in exclusions}),
        'collection_error_count': len(errors),
        'zero_volume_price_guard_count': sum(x[-1] == 'price_zero_latest_volume' for x in errors),
        'other_collection_error_count': sum(x[-1] != 'price_zero_latest_volume' for x in errors),
        'collection_error_stage_counts': dict(Counter(x[1] for x in errors)),
        'pages': pages,
        'file_sha256': {name: sha256(value).hexdigest() for name, value in raw.items()},
        'operational_acceptance_complete': False,
        'failed_refresh_evidence': failed_refresh,
        'remaining_acceptance_evidence': remaining,
        'investment_score_certified': False, 'performance_validated': False,
        'provider_calls': 0, 'read_only': True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs', type=Path, default=ROOT / 'docs')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--verify-failed-refresh', action='store_true')
    args = parser.parse_args()
    incident = failed_refresh_evidence(ROOT, FAILED_REFRESH) if args.verify_failed_refresh else None
    result = audit(args.docs, failed_refresh=incident)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        args.out.write_text(payload, encoding='utf-8')
    print(payload, end='')
    if (not result['publication_coherent']
            or (incident and incident['status'] != 'verified_preserved')):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
