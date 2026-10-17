"""Offline, fail-closed review of previously captured private XBRL evidence.

This connects a same-period KRW XBRL candidate and its explicit SeparateMember
axis with a roleType-backed separate income statement presentation clue.
Only aggregate, previously observed facts are available in the input report;
it CANNOT prove that the specific fact instance belongs to the presentation
linkbase role, validate full DTS, or reconstruct a historical PIT snapshot.

No network calls, no scoring, no publication and no raw-fact export.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from .pinned_2025_pilot import PINNED_CASES, PINNED_ASOF

MAX_PRIVATE_INPUT = 5_000_000
PRIMARY_ABSTRACT = 'StatementOfComprehensiveIncomeAbstract'
HEX16 = re.compile(r'[a-f0-9]{16}\Z')


def _safe_result(status: str, *, concept=None, namespace=None, role=None):
    return {
        'status': status,
        'research_primary_statement_candidate_concept': concept,
        'candidate_namespace_sha256_prefix': namespace,
        'candidate_role_sha256_prefix': role,
        'individual_fact_to_presentation_role_link_verified': False,
        'full_dts_verified': False,
        'ofs_scope_certified': False,
        'numeric_pit_snapshot_verified': False,
        'confirmed_scoring_allowed': False,
        'investment_data_ready': False,
    }


def examine_revenue_report(report: dict) -> dict:
    """Produce a conservative *candidate* selection, never a verified fact."""
    if not isinstance(report, dict):
        return _safe_result('input_missing_or_invalid')
    if not (report.get('receipt_latest_asof') is True
            and report.get('financial_api_receipt_matches') is True
            and report.get('xbrl_original_archive_observed') is True
            and report.get('original_receipt_precedes_asof_date_only') is True
            and report.get('amendment_observed') is False
            and report.get('fs_div') == 'OFS'
            and report.get('business_year') == 2025
            and report.get('primary_statement_role_verified') is False
            and report.get('embedded_role_type_definitions_complete_dts_verified') is False
            and report.get('historical_numeric_snapshot_verified') is False
            and report.get('confirmed_scoring_allowed') is False):
        return _safe_result('identity_revision_or_safety_gate_failed')

    metrics = report.get('metrics')
    metric = metrics.get('revenue') if isinstance(metrics, dict) else None
    if not isinstance(metric, dict) or metric.get('scoring_allowed') is not False:
        return _safe_result('missing_or_unsafe_revenue_metric')
    groups = metric.get('xbrl_scope_pattern_concept_groups')
    if (not isinstance(groups, list) or not groups
            or len(groups) > 12
            or metric.get('xbrl_requested_scope_pattern_is_verified') is not False):
        return _safe_result('unusable_scope_candidate_summary')
    counts = ('fact_count', 'api_amount_equal_count',
              'distinct_context_count', 'distinct_value_count')
    if any(not isinstance(g, dict)
           or any(type(g.get(k)) is not int or g[k] < 0 for k in counts)
           for g in groups):
        return _safe_result('malformed_candidate_counts')
    if (sum(g['fact_count'] for g in groups)
            != metric.get('xbrl_requested_scope_pattern_only_candidate_count')
            or sum(g['api_amount_equal_count'] for g in groups)
            != metric.get('xbrl_requested_scope_pattern_only_api_equal_count')
            or sum(g['api_amount_equal_count'] for g in groups) != 1):
        return _safe_result('multiple_or_inconsistent_api_value_matches')
    # The XBRL parser already filters the year duration, pure iso4217:KRW,
    # and explicit context dimensions; this private artifact holds only
    # concept-level summaries rather than the full individual fact identity.
    revenue = [g for g in groups
               if g.get('concept_local') == 'Revenue'
               and g['fact_count'] == 1
               and g['api_amount_equal_count'] == 1
               and g['distinct_context_count'] == 1
               and g['distinct_value_count'] == 1
               and isinstance(g.get('namespace_sha256_prefix'), str)
               and HEX16.fullmatch(g['namespace_sha256_prefix'])]
    if len(revenue) != 1:
        return _safe_result('unique_revenue_fact_not_established')

    roles = report.get('revenue_embedded_presentation_role_samples')
    if (not isinstance(roles, list) or not roles
            or report.get('revenue_embedded_presentation_role_samples_truncated') is not False):
        return _safe_result('presentation_evidence_missing_or_truncated')
    matched = []
    competing = []
    for role in roles:
        if not isinstance(role, dict):
            return _safe_result('invalid_presentation_role')
        hints = role.get('embedded_role_definition_hints_unverified', [])
        parents = role.get('direct_parent_concept_local_samples', [])
        if not isinstance(hints, list) or not isinstance(parents, list):
            return _safe_result('invalid_presentation_role')
        candidate = (
            role.get('embedded_role_type_found') is True
            and role.get('embedded_role_type_used_on_presentation') is True
            and role.get('parent_sample_truncated') is False
            and type(role.get('as_child_arc_count')) is int
            and role['as_child_arc_count'] >= 1
            and PRIMARY_ABSTRACT in parents
            and 'income_statement' in hints
            and 'separate' in hints
            and 'consolidated' not in hints
            and 'notes' not in hints
            and isinstance(role.get('role_uri_sha256_prefix'), str)
            and HEX16.fullmatch(role['role_uri_sha256_prefix'])
        )
        if candidate:
            if role.get('concept_local') == 'Revenue':
                matched.append(role)
            else:
                competing.append(role)
    if competing or len(matched) != 1:
        return _safe_result('primary_role_evidence_ambiguous_or_absent')
    return _safe_result(
        'unique_revenue_primary_candidate_research_only',
        concept='Revenue',
        namespace=revenue[0]['namespace_sha256_prefix'],
        role=matched[0]['role_uri_sha256_prefix'])


def audit_private_cohort(data: dict) -> dict:
    """Only the exact pinned, previously observed cohort is eligible."""
    if not isinstance(data, dict):
        raise ValueError('Invalid private research JSON document')
    dart = data.get('dart')
    items = dart.get('companies_sampled') if isinstance(dart, dict) else None
    if (data.get('asof') != PINNED_ASOF
            or data.get('mode') != 'live'
            or data.get('investment_data_ready') is not False
            or data.get('confirmed_scoring_allowed') is not False
            or not isinstance(items, list)
            or [x.get('code') for x in items if isinstance(x, dict)]
               != list(PINNED_CASES)
            or len(items) != len(PINNED_CASES)):
        raise ValueError('Private evidence is incomplete or not the reviewed cohort')

    outcome = []
    for item in items:
        code = item['code']
        corp, receipt, scope = PINNED_CASES[code]
        report = item.get('xbrl_comparison')
        if (item.get('corp_code') != corp or not isinstance(report, dict)
                or report.get('rcept_no') != receipt
                or report.get('fs_div') != scope
                or report.get('corp_code') != corp):
            raise ValueError('Report identity does not match pinned observation')
        result = examine_revenue_report(report)
        outcome.append({'code': code, **result})
    return {
        'schema': 'offline-revenue-evidence-0.1',
        'source_asof': PINNED_ASOF,
        'source_kind': 'previously_observed_private_xbrl_summary',
        'companies_evaluated': len(outcome),
        'primary_revenue_candidates_only': sum(
            r['status'] == 'unique_revenue_primary_candidate_research_only'
            for r in outcome),
        'per_company': outcome,
        'all_six_metric_accounting_scope_verified': False,
        'original_historical_values_pit_certified': False,
        'confirmed_scoring_allowed': False,
        'investment_data_ready': False,
        'next_missing_evidence': [
            'Bind selected numeric fact QName and contextRef to a verified full DTS primary role',
            'Verify exact legal entity and OFS/CFS semantic axis, not just local names',
            'Reconstruct and authenticate raw numbers as available at the historical cutoff',
            'Validate exact filing availability time for same-day investment eligibility',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline private XBRL research audit; NO official API')
    parser.add_argument('private_json')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    path = Path(args.private_json)
    if not path.is_file() or path.stat().st_size > MAX_PRIVATE_INPUT:
        parser.error('Private research file missing or exceeds input size limit')
    try:
        evidence = json.loads(path.read_text(encoding='utf-8'))
        result = audit_private_cohort(evidence)
    except (ValueError, OSError, UnicodeError):
        parser.error('Private research file failed strict offline validation')
    if args.output:
        destination = Path(args.output)
        if ('..' in destination.parts or 'docs' in destination.parts
                or destination.is_absolute()):
            parser.error('Offline evidence may not be written to docs or unsafe paths')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False,
                                          indent=2), encoding='utf-8')
    print(json.dumps({
        'companies_evaluated': result['companies_evaluated'],
        'primary_revenue_candidates_only': result['primary_revenue_candidates_only'],
        'scoring_allowed': False, 'pit_verified': False,
    }, ensure_ascii=False))
    return result


if __name__ == '__main__':
    main()
