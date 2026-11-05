"""Bounded PRIVATE candidate review with per-run immutable-source reuse.

No provider calls, no disk cache, no cached mutable cursors/output reports.
Existing validators, source readbacks and compare-and-swap writes still run.
"""
from hashlib import sha1
import json
import os
import re
import time

from v3_dart_continuous_catchup import QuotaWaitingPrivateGitStorage
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError, storage_diagnostic
import v3_private_dart_krx_event_crosscheck as monthly
import v3_private_dart_krx_adjacent_month as adjacent
import v3_private_dart_krx_filing_title_triage as title

MAX_CACHE_BYTES = 64 * 1024 * 1024
IMMUTABLE = re.compile(
    r'(?:derived/dart-filing-receipts/\d{4}-\d{2}/[BCEI]/\d{4}\.json\.gz|'
    r'derived/quality/batches/\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.json\.gz|'
    r'derived/quality/price-discontinuities-\d{4}-\d{2}-\d{2}\.json\.gz)\Z')
PHASES = (
    ('monthly', monthly.run, 'private_unverified_candidate_month_completed'),
    ('adjacent', adjacent.run, 'private_unverified_adjacent_month_pair_completed'),
    ('title', title.run, 'private_title_keyword_shortlist_completed'),
)
WAITING = frozenset((
    'waiting_for_complete_official_filing_month', 'waiting_for_private_risk_review',
    'waiting_for_full_month_krx_risk_review', 'waiting_for_two_complete_adjacent_dart_months',
    'waiting_for_verified_krx_risk_review', 'waiting_for_full_second_month_krx_risk_review',
    'waiting_for_two_approved_complete_months', 'waiting_for_verified_krx_risk_chain',
    'waiting_for_complete_second_month_krx_review'))


class CandidateCatchupError(ValueError):
    """Fixed public codes only."""


class ReusingPrivateStorage(QuotaWaitingPrivateGitStorage):
    """Reuse only content-address-checked immutable input bytes in this process.

    Checkpoints, repository metadata, directory inventories and every generated
    report always use the live transport. Validators still check source hashes,
    page identities and cursor stability for every transaction.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._inputs = {}
        self.cache_bytes = self.cache_hits = 0

    def get_content(self, path):
        if path in self._inputs:
            self.cache_hits += 1
            return self._inputs[path]
        raw, version = super().get_content(path)
        if IMMUTABLE.fullmatch(path) and raw is not None:
            if (type(raw) is not bytes or type(version) is not str or
                sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != version):
                raise PrivateHistoryError('private_candidate_input_blob_mismatch')
            if len(self._inputs) < 10000 and self.cache_bytes + len(raw) <= MAX_CACHE_BYTES:
                self._inputs[path] = raw, version
                self.cache_bytes += len(raw)
        # Missing input is never cached: a later committed page may appear.
        return raw, version


def catchup(storage, *, segments=4, phases=None, clock=None, deadline=None, emit=None):
    if type(segments) is not int or not 1 <= segments <= 4:
        raise CandidateCatchupError('invalid_candidate_segment_limit')
    clock = time.monotonic if clock is None else clock
    phases = PHASES if phases is None else phases
    completed = 0
    for name, collect, success in phases:
        for _ in range(segments):
            if deadline is not None and clock() >= deadline:
                return {'completed_segments': completed, 'stop_reason': 'time_limit', 'provider_calls': 0}
            result = collect(storage)
            if (type(result) is not dict or result.get('provider_calls') != 0 or
                result.get('scores_promoted') != 0 or result.get('confirmed_corporate_actions') != 0):
                raise CandidateCatchupError('untrusted_candidate_catchup_result')
            status = result.get('status')
            if status != success and status not in WAITING:
                raise CandidateCatchupError('untrusted_candidate_catchup_status')
            if status == success:
                completed += 1
            if emit:
                emit({'phase': name, 'status': status, 'completed_segments': completed,
                      'provider_calls': 0, 'scores_promoted': 0})
            if status != success:
                break  # A waiting cursor is never polled four times in a row.
    return {'completed_segments': completed, 'stop_reason': 'bounded_phases_finished', 'provider_calls': 0}


def main():
    try:
        if (os.getenv('V3_PRIVATE_ARCHIVE_REPO') != monthly.PRIVATE_REPO or
            len(os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', '')) < 20):
            raise CandidateCatchupError('private_candidate_credentials_missing')
        segments = int(os.getenv('DART_REVIEW_SEGMENTS', '4'))
        if not 1 <= segments <= 4:
            raise CandidateCatchupError('invalid_candidate_segment_limit')
        deadline = time.monotonic() + 15 * 60
        storage = ReusingPrivateStorage(monthly.PRIVATE_REPO,
            os.environ['V3_PRIVATE_ARCHIVE_TOKEN'], deadline=deadline)
        monthly.verify_private_risk_archive(storage)
        result = catchup(storage, segments=segments, deadline=deadline,
            emit=lambda x: print(json.dumps(x, sort_keys=True), flush=True))
        print(json.dumps({**result, 'immutable_input_reads_reused': storage.cache_hits,
                          'memory_cache_bytes': storage.cache_bytes, 'scores_promoted': 0}, sort_keys=True))
        return 0
    except Exception as error:
        reason = (monthly._safe_failure_code(error) if type(error) is monthly.CrosscheckError else
                  'private_candidate_storage_unavailable' if isinstance(error, PrivateHistoryError) else
                  'private_candidate_validation_failed')
        print(json.dumps({'status': 'blocked', 'reason': reason,
            **(storage_diagnostic(error) if isinstance(error, PrivateHistoryError) else {}),
            'source_values_exposed': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
