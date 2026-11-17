"""Validate explicit source operations without relabelling saved observations."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import subprocess

from v3_aftermarket_cutoff import KST, target_policy

ALLOWED = {
    'full-market-batches.yml': 'publish',
    'v3-current-observed-scorecard.yml': 'write',
    'v3-operational-research-score.yml': 'replay',
    'v3-ma240-source-completion.yml': 'recover',
    'v3-retained-name-recovery.yml': 'recover',
    'v3-rolling-dart-annual-book.yml': 'collect',
    'v3-dart-bulk-four-year.yml': 'collect',
    'v3-regular-close-indicators.yml': 'derive',
}


class SourceRequestError(ValueError):
    """Fixed diagnostics for public workflow logs."""


def prepare(request, catalog, repository, *, now=None):
    if (not isinstance(request, dict)
            or set(request) not in ({'id', 'asof', 'workflow', 'mode'},
                                   {'id', 'asof', 'workflow', 'mode', 'restore_schedule'})
            or not isinstance(catalog, dict)
            or not isinstance(repository, str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)):
        raise SourceRequestError('invalid_source_request')
    workflow = request['workflow']
    if not isinstance(workflow, str) or ALLOWED.get(workflow) != request['mode']:
        raise SourceRequestError('source_operation_not_allowed')
    if not isinstance(request['id'], str) or not 1 <= len(request['id']) <= 80:
        raise SourceRequestError('invalid_source_request_id')
    restore = request.get('restore_schedule', False)
    full_market = workflow == 'full-market-batches.yml'
    if type(restore) is not bool or (restore and not full_market):
        raise SourceRequestError('invalid_schedule_restore_request')
    try:
        requested = date.fromisoformat(request['asof'])
        saved = date.fromisoformat(catalog['date'])
        if requested.isoformat() != request['asof'] or saved.isoformat() != catalog['date']:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise SourceRequestError('invalid_source_request_date') from None
    now = datetime.now(KST) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise SourceRequestError('source_request_timezone_required')
    if requested > now.astimezone(KST).date():
        raise SourceRequestError('future_source_request')
    delta = (requested - saved).days
    if delta != 0 and (not full_market or not 0 < delta <= 7):
        raise SourceRequestError('source_request_date_mismatch')
    if full_market:
        if requested.weekday() >= 5:
            raise SourceRequestError('source_request_weekend')
        if not target_policy(request['asof'], now=now)['complete_session_by_clock']:
            raise SourceRequestError('source_request_session_not_complete')
    command = ['gh', 'workflow', 'run', workflow, '--repo', repository, '--ref', 'main']
    if request['mode'] in ('publish', 'write'):
        command += ['-f', 'mode=' + request['mode']]
    if full_market:
        command += ['-f', 'asof=' + request['asof']]
    elif workflow == 'v3-dart-bulk-four-year.yml':
        command += ['-f', 'mode=live', '-f', 'groups=3']
    return command, {
        'requested': workflow, 'asof': request['asof'], 'request_id': request['id'],
        'previous_catalog_asof': catalog['date'],
        'new_session_requested': delta > 0,
        'date_pinned_roster_verification_required': full_market,
        'restore_schedule_requested': restore,
        'collection_completed': False,
        'points_added_by_dispatch': 0,
    }


def execute(request, catalog, repository, *, now=None, run=None):
    run = subprocess.run if run is None else run
    command, report = prepare(request, catalog, repository, now=now)
    if report['restore_schedule_requested']:
        result = run(['gh', 'api', 'repos/' + repository +
                      '/actions/workflows/full-market-batches.yml'],
                     check=True, capture_output=True, text=True)
        try:
            workflow = json.loads(result.stdout)
            if workflow['path'] != '.github/workflows/full-market-batches.yml':
                raise ValueError()
            state = workflow['state']
            if state not in ('active', 'disabled_inactivity', 'disabled_manually'):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise SourceRequestError('source_workflow_state_unverified') from None
        report['previous_workflow_state'] = state
        if state != 'active':
            run(['gh', 'workflow', 'enable', 'full-market-batches.yml', '--repo', repository],
                check=True)
            report['schedule_restored'] = True
        else:
            report['schedule_restored'] = False
    run(command, check=True)
    return report


def main():
    request = json.loads(Path('ops/source-acquisition-request.json').read_bytes())
    catalog = json.loads(Path('docs/v22-market-catalog.json').read_bytes())
    report = execute(request, catalog, os.environ['GITHUB_REPOSITORY'])
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
