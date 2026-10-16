"""Audit the exact original 53 v2.1 failures, independent of today's candidates.

The source Git blob hash pins the byte-for-byte historical archive in the repository.
A contiguous numeric suffix is NOT considered verified full history or KRX session.
"""
import hashlib
import json
from pathlib import Path

BASELINE_SOURCE = 'docs/archive/quant_2026-10-14_214356.json'
BASELINE_GIT_BLOB_SHA = 'af36e077b2c426a551ac0f073b035b6bc0a5a141'


def git_blob_sha(raw: bytes) -> str:
    """Return Git's content object SHA-1, *not* a plain file SHA-1."""
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def load_cohort(path=Path('config/v22_baseline_53.json')):
    path = Path(path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    # The path and hash must be pinned, not editable alongside a new rotating list.
    if manifest.get('source') != BASELINE_SOURCE:
        raise ValueError('Original baseline source path changed')
    if manifest.get('source_git_blob_sha') != BASELINE_GIT_BLOB_SHA:
        raise ValueError('Original baseline Git blob ID changed')
    source = path.parent.parent / BASELINE_SOURCE
    if not source.is_file():
        raise FileNotFoundError(f'Historical baseline source missing: {source}')
    raw = source.read_bytes()
    if git_blob_sha(raw) != BASELINE_GIT_BLOB_SHA:
        raise ValueError('Original baseline source bytes changed')
    snapshot = json.loads(raw)
    if snapshot.get('asof') != '2026-10-14':
        raise ValueError('Original baseline date changed')
    expected = {s['code']: s['name'] for s in snapshot['stocks']
                if s.get('status') == 'insufficient'}
    rows = manifest['stocks']
    codes = [s['code'] for s in rows]
    if len(codes) != 53 or len(set(codes)) != 53 or set(codes) != set(expected):
        raise ValueError('Original 53-symbol cohort mismatch')
    if any(s.get('name') != expected[s['code']] for s in rows):
        raise ValueError('Original cohort name differs from the archived source')
    if any(s.get('market') not in ('KOSPI', 'KOSDAQ') for s in rows):
        raise ValueError('Original cohort exchange missing')
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
