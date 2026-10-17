"""Mark incomplete five-company research as FAILED, without scoring promotion."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .financial import ACCOUNTS
from .pinned_2025_pilot import PINNED_CASES, PINNED_ASOF

PINNED_PROVENANCE = 'two_prior_official_probe_mapping_observations_not_live'


def check_five_company_research(report: dict) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(report, dict):
        return False, ['invalid_report']
    dart = report.get('dart') or {}
    pilot = dart.get('xbrl_batch_pilot') or {}
    companies = dart.get('companies_sampled') or []
    expected_codes = tuple(PINNED_CASES)
    if (report.get('asof') != PINNED_ASOF
            or tuple(report.get('requested_symbols') or ()) != expected_codes
            or report.get('mode') != 'live'):
        errors.append('wrong_research_cohort')
    if (dart.get('status') != 'observed_unverified'
            or pilot.get('requested_cases') != 5
            or pilot.get('completed_case_reports') != 5
            or pilot.get('company_mapping_provenance') != PINNED_PROVENANCE
            or report.get('investment_data_ready') is not False
            or report.get('confirmed_scoring_allowed') is not False):
        errors.append('incomplete_or_unsafe_batch')
    if not isinstance(companies, list) or tuple(
            c.get('code') if isinstance(c, dict) else None for c in companies
            ) != expected_codes:
        errors.append('missing_or_misordered_companies')
        return False, errors
    for co in companies:
        code = co['code']
        corp, receipt, scope = PINNED_CASES[code]
        x = co.get('xbrl_comparison') or {}
        metrics = x.get('metrics')
        ok = (
            co.get('corp_code') == corp
            and x.get('rcept_no') == receipt
            and x.get('fs_div') == scope
            and x.get('financial_api_receipt_matches') is True
            and x.get('receipt_latest_asof') is True
            and x.get('xbrl_original_archive_observed') is True
            and x.get('investment_data_ready') is False
            and x.get('confirmed_scoring_allowed') is False
            and x.get('xbrl_accounting_scope_verified') is False
            and isinstance(metrics, dict)
            and set(metrics) == set(ACCOUNTS)
            and all(isinstance(metric, dict) and metric.get('status')
                    and metric.get('scoring_allowed') is False
                    for metric in metrics.values())
        )
        if not ok:
            errors.append('missing_or_unverified_report_' + code)
    return not errors, errors


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print('::error::Research artifact path is required.')
        return 2
    path = Path(args[0])
    if not path.is_file():
        print('::error::Five-company research output is missing.')
        return 1
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError, OSError):
        print('::error::Five-company research output cannot be safely parsed.')
        return 1
    success, errors = check_five_company_research(data)
    if not success:
        # Fixed safe labels only; never emit API credentials or raw XML.
        print('::error::Five-company research INCOMPLETE: ' + ','.join(errors))
        print('::error::Investment scoring remains BLOCKED.')
        return 1
    print('::notice::Five-company research COLLECTED (5/5); '
          'scope/PIT remain UNVERIFIED and investment scoring BLOCKED.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
