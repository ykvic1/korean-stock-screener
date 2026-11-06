"""Retry a failed Pages publication without manufacturing score changes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ACTIVE = frozenset(('queued', 'in_progress', 'waiting', 'requested', 'pending'))
RETRYABLE = frozenset(('failure', 'cancelled', 'timed_out'))


def needs_retry(payload):
    """GitHub returns runs newest first; an older active run blocks retries."""
    if (type(payload) is not dict or type(payload.get('workflow_runs')) is not list
        or len(payload['workflow_runs']) > 100):
        raise ValueError('unverified_pages_workflow_runs')
    runs = payload['workflow_runs']
    if not runs:
        return False
    for run in runs:
        if (type(run) is not dict or run.get('name') != 'pages build and deployment'
            or type(run.get('status')) is not str
            or run.get('status') not in ACTIVE | {'completed'}):
            raise ValueError('unverified_pages_workflow_run')
        if run['status'] == 'completed' and type(run.get('conclusion')) is not str:
            raise ValueError('unverified_pages_workflow_conclusion')
    if any(run['status'] in ACTIVE for run in runs):
        return False
    return runs[0]['conclusion'] in RETRYABLE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', required=True)
    args = parser.parse_args()
    print(str(needs_retry(json.loads(Path(args.runs).read_text()))).lower())


if __name__ == '__main__':
    main()
