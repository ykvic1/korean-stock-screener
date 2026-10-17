"""Offline evidence census for receipt-bound ORIGINAL XBRL numbers; never PIT approval.

This deliberately distinguishes observed values in an original receipt ZIP
retrieved now from proof of those bytes' availability at a historical cutoff.
No raw values appear in the summary or logs. Input remains a PRIVATE artifact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .financial import ACCOUNTS
from .pinned_2025_pilot import PINNED_ASOF, PINNED_CASES

MAX_PRIVATE_JSON_BYTES = 5_000_000
MAX_CANDIDATES_PER_METRIC = 12


def _examined_metric(metric):
    fail = lambda label: {'state':label, 'original_candidate_count':0,
                          'matched_current_api_count':0}
    if not isinstance(metric, dict) or metric.get('scoring_allowed') is not False:
        return fail('metric_missing_or_unsafe')
    group = metric.get('original_receipt_numeric_candidates_private')
    if not isinstance(group, list) or not 1 <= len(group) <= MAX_CANDIDATES_PER_METRIC:
        return fail('receipt_original_values_missing_or_unbounded')
    if (metric.get('original_receipt_numeric_candidates_count') != len(group)
            or metric.get('original_receipt_numeric_candidates_scope_certified') is not False
            or metric.get('original_receipt_numeric_candidates_pit_certified') is not False
            or metric.get('xbrl_requested_scope_pattern_only_candidate_count') != len(group)):
        return fail('candidate_count_or_certification_inconsistent')
    matches = 0
    for candidate in group:
        if (not isinstance(candidate, dict)
                or candidate.get('source') != 'original_xbrl_zip_by_receipt'
                or type(candidate.get('original_xbrl_amount_krw')) is not int
                or candidate.get('unit_verified_krw') is not True
                or candidate.get('fiscal_period_verified') is not True
                or candidate.get('dimension_axes_exactly_separate_pattern_local_only') is not True
                or candidate.get('issuer_identity_verified_against_corp_code') is not False
                or candidate.get('original_zip_immutability_at_historical_date_verified') is not False
                or candidate.get('complete_dts_and_fs_scope_verified') is not False
                or candidate.get('historical_pit_numeric_value_certified') is not False
                or candidate.get('confirmed_scoring_allowed') is not False
                or candidate.get('current_api_amount_equal') not in (True, False)):
            return fail('original_candidate_malformed_or_overcertified')
        matches += candidate['current_api_amount_equal'] is True
    if (matches != metric.get('xbrl_requested_scope_pattern_only_api_equal_count')):
        return fail('current_api_comparison_inconsistent')
    return {'state':'receipt_bound_original_value_candidates_collected_scope_unverified',
            'original_candidate_count':len(group),
            'matched_current_api_count':int(matches)}


def inspect_private_original_numbers(data):
    """Summarize research evidence with NO original numbers in the output."""
    if not isinstance(data, dict):
        raise ValueError('Invalid private research object')
    dart = data.get('dart')
    companies = dart.get('companies_sampled') if isinstance(dart, dict) else None
    if (data.get('asof') != PINNED_ASOF
            or data.get('mode') != 'live'
            or data.get('investment_data_ready') is not False
            or data.get('confirmed_scoring_allowed') is not False
            or not isinstance(companies, list)
            or len(companies) != len(PINNED_CASES)
            or [x.get('code') if isinstance(x, dict) else None
                for x in companies] != list(PINNED_CASES)):
        raise ValueError('Not the exact approved private five-firm research cohort')
    results = []
    complete = 0
    total_candidate_count = 0
    api_matches = 0
    for row in companies:
        code = row['code']
        corp, receipt, scope = PINNED_CASES[code]
        comp = row.get('xbrl_comparison')
        if (row.get('corp_code') != corp or not isinstance(comp, dict)
                or comp.get('corp_code') != corp
                or comp.get('rcept_no') != receipt
                or comp.get('fs_div') != scope
                or comp.get('business_year') != 2025):
            raise ValueError('Receipt/company/accounting-request identity mismatch')
        prerequisites = (
            comp.get('xbrl_original_archive_observed') is True
            and comp.get('receipt_latest_asof') is True
            and comp.get('original_receipt_precedes_asof_date_only') is True
            and comp.get('amendment_observed') is False
            and comp.get('original_numeric_values_source') == 'receipt_specific_xbrl_zip'
            and comp.get('original_numeric_values_separate_from_current_financial_api') is True
            and comp.get('historical_receipt_zip_content_immutability_verified') is False
            and comp.get('original_numeric_values_eligible_for_pit_backtest') is False
            and comp.get('historical_numeric_snapshot_verified') is False
            and comp.get('confirmed_scoring_allowed') is False
            and comp.get('investment_data_ready') is False)
        per_metric = []
        for name in ACCOUNTS:
            metric = ((comp.get('metrics') or {}).get(name)
                      if isinstance(comp.get('metrics'), dict) else None)
            state = _examined_metric(metric)
            per_metric.append({'metric':name, **state})
            if prerequisites and state['state'] == (
                    'receipt_bound_original_value_candidates_collected_scope_unverified'):
                complete += 1
                total_candidate_count += state['original_candidate_count']
                api_matches += state['matched_current_api_count']
        results.append({'code':code, 'receipt_and_cutoff_date_only_observed':prerequisites,
                        'metric_census':per_metric,
                        'accounting_scope_verified':False,
                        'historical_numeric_pit_certified':False})
    return {
        'schema':'receipt-bound-pit-evidence-audit-0.1',
        'asof':PINNED_ASOF,
        'company_count':len(results),
        'metric_count':len(PINNED_CASES)*len(ACCOUNTS),
        'metrics_with_receipt_bound_numeric_candidates':complete,
        'private_original_numeric_candidates_observed':total_candidate_count,
        'candidates_matching_current_api':api_matches,
        'per_company':results,
        'numeric_values_written_to_summary':False,
        'filing_cutoff_granularity':'calendar_date_only',
        'full_xbrl_dts_statement_and_entity_scope_verified':False,
        'original_zip_bytes_authenticated_at_historical_cutoff':False,
        'historical_numeric_pit_certified':False,
        'confirmed_scoring_allowed':False,
        'investment_data_ready':False,
        'blocking_reasons':[
            'Original archive downloaded now is not independently attested as historical bytes',
            'SeparateMember local-name hint does not certify legal entity/OFS statement scope',
            'No filing-time and historical cutoff timestamp verification',
            'Current finance API is not a historical versioned financial snapshot',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Private, offline receipt-bound original-number PIT safety census')
    parser.add_argument('private_json')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    path = Path(args.private_json)
    output = Path(args.output)
    if (not path.is_file() or path.stat().st_size > MAX_PRIVATE_JSON_BYTES
            or path.stat().st_size == 0):
        parser.error('Private JSON missing or outside bounded size limit')
    if (output.is_absolute() or '..' in output.parts or
            'docs' in output.parts or not output.parts):
        parser.error('Output must remain in a private non-docs relative directory')
    try:
        evidence = json.loads(path.read_text(encoding='utf-8'))
        report = inspect_private_original_numbers(evidence)
    except (OSError, UnicodeError, ValueError):
        parser.error('Private PIT evidence could not be safely audited')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding='utf-8')
    print('::notice::Receipt-bound original-value candidate records checked='
          +str(report['metrics_with_receipt_bound_numeric_candidates'])
          +' / '+str(report['metric_count'])
          +'; accounting scope and historical PIT remain NOT VERIFIED;'
          +' scoring BLOCKED.')
    if report['metrics_with_receipt_bound_numeric_candidates'] != report['metric_count']:
        # The research output has been written privately for diagnosis; fail
        # the workflow so an incomplete original number set is NEVER success.
        print('::error::Receipt-specific original numeric evidence INCOMPLETE;'
              ' inspect the private research artifact.')
        raise SystemExit(1)
    return report


if __name__ == '__main__':
    main()
