"""Initial collection closeout with accepted missing data; no score certification."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

SCHEMA = 'v3-initial-data-collection-closeout-1'
OUTPUT = 'v3-data-collection-closeout.json'
BANDS = {'100', '95_to_under_100', '90_to_under_95', '80_to_under_90',
         '70_to_under_80', '60_to_under_70', 'under_60'}
CHECKS = ('publication_coherent', 'available_fcf_sources_attempted',
          'historical_receipt_collection_complete', 'fixed_krx_window_extracted')


def valid(record, current_day):
    """A dated project milestone persists across subsequent daily refreshes."""
    from datetime import date
    if not isinstance(record, dict):
        return False
    try:
        closed = record['asof']
        if date.fromisoformat(closed).isoformat() != closed or closed > current_day:
            return False
        date.fromisoformat(current_day)
        counts = record['available_weight_distribution']
        total = record['listed_total']
        if (type(total) is not int or total <= 0 or not isinstance(counts, dict) or
                set(counts) != BANDS or any(type(n) is not int or n < 0 for n in counts.values()) or
                sum(counts.values()) != total):
            return False
        return (record.get('schema') == SCHEMA and
                record.get('status') == 'complete_with_missing_data_excluded' and
                record.get('initial_data_collection_complete') is True and
                record.get('missing_data_excluded_from_completion') is True and
                record.get('all_listed_100_weight_required') is False and
                record.get('missing_values_filled_or_weights_normalized') is False and
                record.get('daily_refresh_continues') is True and
                record.get('investment_score_certified') is False and
                record.get('performance_validated') is False and
                record.get('v30b_release_ready') is False and
                all(record.get('checks', {}).get(k) is True for k in CHECKS) and
                record.get('source_eligible_unfetched_fcf_company_years') == 0 and
                record.get('historical_receipt_completed_months') == 120 and
                record.get('fixed_krx_completed_windows') == record.get('fixed_krx_target_windows') == 490)
    except (KeyError, TypeError, ValueError):
        return False


def build(docs):
    from v3_operational_publication_audit import audit
    evidence = audit(docs)
    names = ('v3-dart-cashflow-gap-summary.json', 'v3-dart-historical-receipt-progress.json',
             'v3-krx-derived-backfill-progress.json', 'v3-operational-evidence-priority.json')
    raw = {n: (docs/n).read_bytes() for n in names}
    gap, receipts, krx, priority = (json.loads(raw[n]) for n in names)
    # Receipt checkpoint field names are established by the existing release audit.
    from v3_v30b_release_gate import _dart_receipt_counts
    months = _dart_receipt_counts(receipts)
    checks = {
        'publication_coherent': evidence['publication_coherent'],
        'available_fcf_sources_attempted': (gap.get('asof') == evidence['asof'] and
            gap.get('source_eligible_unfetched_company_years') == 0),
        'historical_receipt_collection_complete': months == (120, 0),
        'fixed_krx_window_extracted': (krx.get('five_session_cohorts_completed') ==
            krx.get('five_session_cohorts_target') == 490 and krx.get('source_history_coverage_ratio') == 1),
    }
    if (not all(checks.values()) or priority.get('asof') != evidence['asof'] or
            priority.get('source_report_sha256') != evidence['file_sha256']['v3-operational-research-scores.json']):
        raise ValueError('collection_closeout_evidence_not_aligned')
    result = {
        'schema': SCHEMA, 'asof': evidence['asof'],
        'status': 'complete_with_missing_data_excluded',
        'initial_data_collection_complete': True,
        'completion_basis': 'user_accepted_available_weight_closeout_20261117',
        'missing_data_excluded_from_completion': True,
        'all_listed_100_weight_required': False,
        'missing_values_filled_or_weights_normalized': False,
        'daily_refresh_continues': True,
        'listed_total': evidence['listed_total'],
        'available_weight_distribution': evidence['available_weight_distribution'],
        'incomplete_weight_issuers': priority['incomplete_count'],
        'missing_component_issuer_counts': priority['component_missing_issuer_counts'],
        'missing_reason_component_counts': priority['reason_component_counts'],
        'checks': checks,
        'source_eligible_unfetched_fcf_company_years': 0,
        'historical_receipt_completed_months': 120,
        'fixed_krx_completed_windows': 490, 'fixed_krx_target_windows': 490,
        'source_sha256': evidence['file_sha256'] | {n: sha256(v).hexdigest() for n,v in raw.items()},
        'investment_score_certified': False, 'performance_validated': False,
        'v30b_release_ready': False, 'provider_calls': 0,
    }
    if not valid(result, evidence['asof']):
        raise ValueError('invalid_collection_closeout')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs', type=Path, default=Path('docs'))
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    result = build(args.docs)
    text = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.write:
        (args.docs/OUTPUT).write_text(text, encoding='utf8')
    print(text, end='')
