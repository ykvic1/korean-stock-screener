"""Offline cross-run evidence review; same-day reproducibility is NOT historical PIT.

Accept 2-8 previously collected manual DART research artifacts. Compare only
strict, receipt-bound SHA256 evidence; never publish numeric values, raw
financial API responses, full XBRL XML, or arbitrary source file paths.
No network access, no score approval, no claims of historical immutability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, date, timezone
import json
from pathlib import Path
import re

from .pinned_2025_pilot import PINNED_ASOF, PINNED_CASES
from .pit_readiness import inspect_private_original_numbers

SHA256 = re.compile(r'[a-f0-9]{64}\Z')
MAX_FILES = 8
MAX_BYTES = 5_000_000


def _when(text):
    if not isinstance(text, str):
        raise ValueError('Missing official research observation timestamp')
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ValueError('Invalid research observation timestamp') from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('Naive research observation timestamp forbidden')
    return moment.astimezone(timezone.utc)


def _one(doc):
    if not isinstance(doc, dict):
        raise ValueError('Research source is not a JSON object')
    timestamp = _when(doc.get('received_at_utc'))
    cutoff = date.fromisoformat(PINNED_ASOF)
    if timestamp.date() <= cutoff:
        # Such a historical observation would require its own independent
        # provenance / timestamp verification; do not silently trust a label.
        raise ValueError('Research timestamp not after cutoff or not independently verified')
    dart = doc.get('dart')
    rows = dart.get('companies_sampled') if isinstance(dart, dict) else None
    pilot = dart.get('xbrl_batch_pilot') if isinstance(dart, dict) else None
    if (doc.get('asof') != PINNED_ASOF
            or doc.get('mode') != 'live'
            or doc.get('confirmed_scoring_allowed') is not False
            or doc.get('investment_data_ready') is not False
            or not isinstance(pilot, dict)
            or pilot.get('completed_case_reports') != 5
            or pilot.get('requested_cases') != 5
            or not isinstance(rows, list) or len(rows) != 5
            or [r.get('code') if isinstance(r, dict) else None for r in rows]
               != list(PINNED_CASES)):
        raise ValueError('Incorrect cohort or incomplete/safety-unsafe manual probe')
    values = {}
    for row in rows:
        stock = row['code']
        corp, receipt, fs_div = PINNED_CASES[stock]
        report = row.get('xbrl_comparison')
        if (row.get('corp_code') != corp
                or not isinstance(report, dict)
                or report.get('corp_code') != corp
                or report.get('rcept_no') != receipt
                or report.get('fs_div') != fs_div
                or report.get('business_year') != 2025
                or report.get('report_code') != '11011'
                or report.get('xbrl_original_archive_observed') is not True
                or report.get('receipt_latest_asof') is not True
                or report.get('amendment_observed') is not False
                or report.get('historical_numeric_snapshot_verified') is not False
                or report.get('xbrl_accounting_scope_verified') is not False
                or report.get('confirmed_scoring_allowed') is not False
                or report.get('investment_data_ready') is not False
                or report.get('filing_list_status') !=
                   'listing_observed_documents_not_requested'):
            raise ValueError('Unsafe receipt/scope or unverified history in input')
        xbrl, api = report.get('xbrl_zip_sha256'), report.get('financial_api_sha256')
        if not (isinstance(xbrl,str) and SHA256.fullmatch(xbrl)
                and isinstance(api,str) and SHA256.fullmatch(api)):
            raise ValueError('Invalid receipt-specific source digest')
        values[stock] = (xbrl,api)
    return timestamp, values


def audit_probe_observations(documents):
    """Date-scoped reproducibility census with an unconditional PIT block."""
    if not isinstance(documents, (tuple,list)) or not 2 <= len(documents) <= MAX_FILES:
        raise ValueError('Must review 2-8 independently collected probe artifacts')
    observations = [_one(doc) for doc in documents]
    times = [moment for moment,_ in observations]
    if len(set(times)) != len(times):
        raise ValueError('Duplicate observation timestamps are not independent evidence')
    newest = documents[max(range(len(observations)), key=lambda i: observations[i][0])]
    observations.sort(key=lambda x:x[0])
    comparison = []
    for stock in PINNED_CASES:
        source_pairs = [obs[1][stock] for obs in observations]
        xbrl_agree = len({pair[0] for pair in source_pairs}) == 1
        api_agree = len({pair[1] for pair in source_pairs}) == 1
        comparison.append({
            'code': stock,
            'xbrl_zip_sha256_stable_in_observation_window': xbrl_agree,
            'current_financial_api_sha256_stable_in_observation_window': api_agree,
            'repeat_observation_count':len(observations),
            'post_cutoff_amendment_search_complete':False,
            'confirmed_historical_original_immutability':False,
            'historical_pit_numeric_eligibility_verified':False,
            'confirmed_scoring_allowed':False,
        })
    # Preserve the newest document's identity even when input order was arbitrary.
    # The newest document may or may not have receipt-specific numbers;
    # keep the originally recorded 30/32 claims separately evidenced.
    try:
        original = inspect_private_original_numbers(newest)
        numeric_count = original['metrics_with_receipt_bound_numeric_candidates']
        candidate_count = original['private_original_numeric_candidates_observed']
        numeric_matches = original['candidates_matching_current_api']
    except (ValueError, TypeError, KeyError):
        numeric_count = candidate_count = numeric_matches = 0
    return {
        'schema':'cross-run-pit-evidence-review-0.1',
        'cutoff_date': PINNED_ASOF,
        'earliest_utc_observation': observations[0][0].isoformat(),
        'latest_utc_observation': observations[-1][0].isoformat(),
        'observation_count':len(observations),
        'distinct_observation_calendar_dates':len({t.date() for t in times}),
        'all_observations_after_cutoff':True,
        'all_five_repeated_sha256_pairs_stable':all(
            p['xbrl_zip_sha256_stable_in_observation_window'] and
            p['current_financial_api_sha256_stable_in_observation_window']
            for p in comparison),
        'companies':comparison,
        'newest_original_numeric_metric_groups': numeric_count,
        'newest_original_numeric_candidates': candidate_count,
        'newest_original_candidates_matching_current_api':numeric_matches,
        'raw_values_or_sha256_digests_exported':False,
        'source_bytes_authenticated_at_historical_cutoff':False,
        'complete_post_cutoff_revision_chain_verified':False,
        'same_day_filing_timestamp_verified':False,
        'full_dts_and_ofs_scope_verified':False,
        'historical_numeric_pit_certified':False,
        'confirmed_scoring_allowed':False,
        'investment_data_ready':False,
        'decision':'consistent_current_retrievals_not_historical_pit',
        'next_required_external_evidence':[
            'independent historical receipt-specific source preservation or attestation',
            'complete applicable amendment chronology across the cutoff',
            'full DTS and legal entity accounting basis validation',
            'disclosure timestamp for any same-day trading eligibility',
        ],
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline, read-only PIT source repeatability review')
    parser.add_argument('private_json',nargs='+',help='2 to 8 existing private probe result JSONs')
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    if not 2 <= len(args.private_json) <= MAX_FILES:
        parser.error('Supply 2-8 distinct existing research outputs')
    out=Path(args.output)
    if (out.is_absolute() or '..' in out.parts or 'docs' in out.parts
            or not out.parts or not out.suffix == '.json'):
        parser.error('Output must be private relative JSON outside docs')
    docs=[]
    for item in args.private_json:
        path=Path(item)
        if (not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES):
            parser.error('Research evidence missing or outside safe size bound')
        try:
            docs.append(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError,UnicodeError,OSError):
            parser.error('Research artifact JSON unreadable')
    try:
        summary=audit_probe_observations(docs)
    except ValueError:
        parser.error('Research sources fail strict receipt/date/scope validation')
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Reproducibility: '+str(int(summary['all_five_repeated_sha256_pairs_stable']))+
          ' across '+str(summary['observation_count'])+' after-cutoff observations; '+
          'historical PIT=false; scoring=false.')
    return summary


if __name__=='__main__':
    main()
