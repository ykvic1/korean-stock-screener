"""Immutable original failure cohort, independent of today's ranked candidates."""
import hashlib
import json
from pathlib import Path


def load_cohort(path=Path('config/v22_baseline_53.json')):
    manifest = json.loads(path.read_text())
    source = Path(manifest['source'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['source_sha256']:
        raise ValueError('Baseline source changed')
    expected = {s['code'] for s in json.loads(source.read_text())['stocks']
                if s.get('status') == 'insufficient'}
    rows = manifest['stocks']
    codes = [s['code'] for s in rows]
    if len(codes) != 53 or len(set(codes)) != 53 or set(codes) != expected:
        raise ValueError('Original 53-symbol cohort mismatch')
    if any(s.get('market') not in ('KOSPI', 'KOSDAQ') for s in rows):
        raise ValueError('Missing baseline exchange')
    return rows


def union_stocks(current, baseline):
    rows = {s['code']: s for s in current}
    for stock in baseline:
        rows.setdefault(stock['code'], stock)
    return list(rows.values())


def cohort_summary(rows):
    codes = [s['code'] for s in rows]
    if len(codes) != 53 or len(set(codes)) != 53:
        raise ValueError('Cannot report recovery rate for incomplete fixed cohort')
    return {
        'requested': 53,
        'fetched': sum('rows' in s for s in rows),
        'numeric_segment_usable': sum(bool(s.get('strict_valid')) for s in rows),
        'independently_matched_recent': sum(bool(s.get('independently_matched')) for s in rows),
        'full_history_verified': 0,
        'regular_session_verified': 0,
        'verification_status': 'unverified',
    }
