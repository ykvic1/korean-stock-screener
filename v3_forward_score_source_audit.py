"""Rebuild recent frozen forward score observations from their pinned Git files."""
from datetime import datetime
from hashlib import sha256
import json
import re
import subprocess

from v3_score_observation_capture import ARCHIVE, MARKET_PATH, SCORE_PATH, capture


MAX_OBSERVATIONS = 5
COMMIT = re.compile(r'[0-9a-f]{40}\Z')


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def verify_observation(observation, score_bytes, market_bytes, committed_at):
    """Compare both source files, capture ordering, and every frozen field."""
    captured_at = datetime.fromisoformat(observation['captured_at_utc'])
    if captured_at.utcoffset() is None or committed_at.utcoffset() is None or committed_at > captured_at:
        raise ValueError('forward_score_source_commit_after_capture')
    if (sha256(score_bytes).hexdigest() != observation['source_score_sha256']
            or sha256(market_bytes).hexdigest() != observation['source_market_sha256']):
        raise ValueError('forward_score_source_sha256_mismatch')
    expected = capture(
        score_bytes, market_bytes, captured_at=captured_at,
        source_commit=observation['source_commit'], run_id=observation['workflow_run_id'],
        run_attempt=observation['workflow_run_attempt'], model_version=observation['model_version'],
    )
    if expected is None or encode(expected) != encode(observation):
        raise ValueError('forward_score_observation_differs_from_saved_sources')
    return {key: expected[key] for key in ('listed_count', 'ranked_count', 'reference_count')}


def audit():
    paths = sorted(ARCHIVE.glob('????-??-??.json'))
    if not paths:
        raise ValueError('forward_score_observations_unavailable')
    observations = {}
    for path in paths[-MAX_OBSERVATIONS:]:
        observation = json.loads(path.read_bytes())
        commit = observation.get('source_commit')
        if (path.stem != observation.get('score_asof') or not isinstance(commit, str)
                or not COMMIT.fullmatch(commit)):
            raise ValueError('invalid_forward_score_observation_reference')
        observations[path.stem] = observation

    commits = {observation['source_commit'] for observation in observations.values()}
    missing = [commit for commit in sorted(commits) if subprocess.run(
        ['git', 'cat-file', '-e', f'{commit}^{{commit}}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode]
    if missing:
        subprocess.run(['git', 'fetch', '--no-tags', '--depth=1', 'origin', *missing],
                       check=True, stdout=subprocess.DEVNULL)
    sessions = {}
    for day, observation in observations.items():
        commit = observation['source_commit']
        committed_at = datetime.fromisoformat(subprocess.check_output(
            ['git', 'show', '-s', '--format=%cI', commit], text=True).strip())
        sessions[day] = verify_observation(
            observation, subprocess.check_output(['git', 'show', f'{commit}:{SCORE_PATH}']),
            subprocess.check_output(['git', 'show', f'{commit}:{MARKET_PATH}']), committed_at)
    return {
        'schema': 'v3-forward-score-git-source-audit-1',
        'available_observation_count': len(paths),
        'audited_observation_count': len(sessions),
        'listed_rows_rebuilt': sum(row['listed_count'] for row in sessions.values()),
        'ranked_rows_rebuilt': sum(row['ranked_count'] for row in sessions.values()),
        'reference_rows_rebuilt': sum(row['reference_count'] for row in sessions.values()),
        'sessions': sessions, 'market_provider_requests': 0,
        'historical_source_chain_certified': False, 'performance_validated': False,
    }


if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=False, sort_keys=True))
