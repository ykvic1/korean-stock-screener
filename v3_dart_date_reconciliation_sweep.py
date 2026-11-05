"""Private, resumable declared-date research over completed source ledgers.

No provider API. Only completed principal AND supplemental pairs are eligible.
Proximity to a price discontinuity never authenticates a corporate action.
"""
from hashlib import sha256
import json
import os
import re
import time

import v3_private_dart_declared_date_proximity as audit
import v3_private_dart_structured_action_receipts as principal
import v3_private_dart_structured_action_extension as supplement
from v3_dart_continuous_catchup import QuotaWaitingPrivateGitStorage
from v3_krx_private_github_backfill import PrivateHistoryError

SCHEMA = 'v3-private-declared-date-sweep-v1'
BASE = 'derived/dart-declared-date-reconciliation'
CHECKPOINT = BASE + '/checkpoint.json'
TARGET = len(audit.MONTHS) - 1
MAX_BATCHES = 32
MAX_SECONDS = 20 * 60
HEX = re.compile(r'[a-f0-9]{64}\Z')
FLAGS = ('actual_completion_verified', 'adjusted_prices_certified', 'historical_pit_certified')
TOTALS = ('reviewed_pair_receipts', 'category_match_observations', 'declared_date_fields',
          'dates_near_risk_window', 'dates_outside_risk_window')


class DateSweepError(ValueError):
    """Fixed codes without source values."""


def _empty():
    return {'schema': SCHEMA, 'next_pair_index': 0, 'next_candidate_index': 0,
            'queue_sha256': None, 'last_file': None, 'last_digest': None,
            'totals': dict.fromkeys(TOTALS, 0), **dict.fromkeys(FLAGS, False), 'scores_promoted': 0}


def _path(pair, start):
    return BASE + f'/reports/{pair:03d}/{start:04d}.json'


def _cursor(raw):
    if raw is None:
        return _empty()
    try:
        if type(raw) is not bytes or not 0 < len(raw) < 8000:
            raise ValueError()
        x = json.loads(raw)
        i, j = x['next_pair_index'], x['next_candidate_index']
        if (set(x) != set(_empty()) or x['schema'] != SCHEMA
            or type(i) is not int or not 0 <= i <= TARGET
            or type(j) is not int or not 0 <= j <= 2000 or i == TARGET and j != 0
            or any(x[k] is not False for k in FLAGS) or x['scores_promoted'] != 0
            or type(x['totals']) is not dict or set(x['totals']) != set(TOTALS)
            or any(type(v) is not int or v < 0 for v in x['totals'].values())
            or x['totals']['dates_near_risk_window'] + x['totals']['dates_outside_risk_window']
                != x['totals']['declared_date_fields']
            or (j == 0 and x['queue_sha256'] is not None)
            or (j > 0 and (type(x['queue_sha256']) is not str or not HEX.fullmatch(x['queue_sha256'])))):
            raise ValueError()
        if i == j == 0:
            if x != _empty():
                raise ValueError()
        else:
            match = re.fullmatch(re.escape(BASE) + r'/reports/(\d{3})/(\d{4})\.json', x['last_file'])
            if (match is None or int(match[1]) != (i if j else i - 1)
                or int(match[2]) >= (j if j else 2001)
                or type(x['last_digest']) is not str or not HEX.fullmatch(x['last_digest'])):
                raise ValueError()
        return x
    except Exception:
        raise DateSweepError('invalid_private_date_sweep_checkpoint') from None


def _tail(storage, state):
    if state['last_file']:
        raw, _ = storage.get_content(state['last_file'])
        if raw is None or sha256(raw).hexdigest() != state['last_digest']:
            raise DateSweepError('private_date_sweep_tail_changed')
        try:
            obj = json.loads(raw)
            if (obj['schema'] != SCHEMA or any(obj[k] is not False for k in FLAGS)
                or obj['scores_promoted'] != 0 or obj['provider_calls'] != 0):
                raise ValueError()
        except Exception:
            raise DateSweepError('invalid_private_date_sweep_tail') from None


def _ready(storage):
    completed = []
    for source in (principal, supplement):
        raw, _ = storage.get_content(source.CHECKPOINT)
        state = source._cursor(raw)
        if state['last_report_file']:
            tail, _ = storage.get_content(state['last_report_file'])
            if tail is None or sha256(tail).hexdigest() != state['last_report_sha256']:
                raise DateSweepError('private_date_sweep_upstream_tail_changed')
        completed.append(state['next_pair_index'])
    return min(completed)


class _ReadWitness:
    """Record only immutable queue/report hashes; never mutable source cursors."""
    def __init__(self, storage, pair):
        self.storage = storage
        self.repository = storage.repository
        self.queue_path = audit.triage_path(pair)
        self.evidence = {}

    def verify_private(self):
        self.storage.verify_private()

    def get_content(self, path):
        raw, version = self.storage.get_content(path)
        if raw is not None and (path == self.queue_path or
            path.startswith(principal.BASE + '/reports/') or
            path.startswith(supplement.BASE + '/reports/')):
            digest = sha256(raw).hexdigest()
            if path in self.evidence and self.evidence[path] != digest:
                raise DateSweepError('private_date_sweep_source_mutated')
            self.evidence[path] = digest
        return raw, version


def sweep(storage, *, max_batches=MAX_BATCHES, max_seconds=MAX_SECONDS, clock=None, emit=None):
    if (getattr(storage, 'repository', None) != audit.PRIVATE_REPO
        or type(max_batches) is not int or not 1 <= max_batches <= MAX_BATCHES
        or type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS):
        raise DateSweepError('invalid_private_date_sweep_scope')
    clock = time.monotonic if clock is None else clock
    if not callable(clock) or emit is not None and not callable(emit):
        raise DateSweepError('invalid_private_date_sweep_callback')
    storage.verify_private()
    deadline = clock() + max_seconds
    batches = examined = 0
    reason = 'batch_limit'
    for _ in range(max_batches):
        if clock() >= deadline:
            reason = 'time_limit'
            break
        raw, _ = storage.get_content(CHECKPOINT)
        if batches and raw != committed:
            raise DateSweepError('private_date_sweep_cursor_changed')
        state = _cursor(raw)
        _tail(storage, state)
        i, j = state['next_pair_index'], state['next_candidate_index']
        ready = _ready(storage)
        if i > ready:
            raise DateSweepError('private_date_sweep_upstream_regressed')
        if i >= ready:
            reason = 'complete' if i == TARGET else 'waiting_for_completed_source_pairs'
            break
        witness = _ReadWitness(storage, i)
        result = audit.read_only_private_audit(witness, pair_index=i, start=j, limit=audit.MAX_BATCH)
        if (result.get('status') != 'private_declared_date_temporal_research'
            or result.get('not_yet_saved_report_slots') != 0
            or result.get('provider_calls') != 0 or result.get('scores_promoted') != 0
            or result.get('zero_provider_calls') is not True
            or any(result.get(k) is not False for k in
                   ('official_actual_completion_verified', 'temporal_match_is_corporate_action_proof',
                    'date_nonmatch_is_absence_of_action_proof', 'adjusted_prices_certified',
                    'historical_pit_issuers_certified', 'private_identity_or_dates_published'))):
            raise DateSweepError('completed_private_date_sources_missing_or_unsafe')
        queue_hash = witness.evidence[witness.queue_path]
        if j and queue_hash != state['queue_sha256']:
            raise DateSweepError('private_date_sweep_queue_changed')
        count = result['selected_unique_receipts']
        end = result['selected_slice_end_offset']
        remaining = result['selected_slice_remaining_receipts']
        if (type(count) is not int or not 0 <= count <= audit.MAX_BATCH or end != j + count
            or type(remaining) is not int or remaining < 0
            or count == 0 and remaining > 0):
            raise DateSweepError('private_date_sweep_stalled')
        path = _path(i, j)
        report = {'schema': SCHEMA, 'source_pair_index': i, 'slice_start': j,
                  'prior_checkpoint_sha256': sha256(raw).hexdigest() if raw else None,
                  'source_hashes_private': witness.evidence, 'audit': result,
                  **dict.fromkeys(FLAGS, False), 'scores_promoted': 0, 'provider_calls': 0}
        encoded = principal.canonical(report)
        principal._put_immutable_report_with_retry(storage, path, encoded)
        principal._same_bytes(storage, path, encoded)
        fields = ('selected_unique_receipts', 'exact_receipt_category_matches',
                  'declared_schedule_date_fields', 'declared_dates_near_krx_risk_window',
                  'declared_dates_outside_krx_risk_window')
        totals = {k: state['totals'][k] + result[f] for k, f in zip(TOTALS, fields)}
        updated = {**state, 'next_pair_index': i + 1 if remaining == 0 else i,
                   'next_candidate_index': 0 if remaining == 0 else end,
                   'queue_sha256': None if remaining == 0 else queue_hash,
                   'last_file': path, 'last_digest': sha256(encoded).hexdigest(), 'totals': totals}
        committed = principal.canonical(updated)
        principal._write_checkpoint_with_retry(storage, raw, committed, checkpoint_path=CHECKPOINT)
        principal._same_bytes(storage, CHECKPOINT, committed)
        batches += 1
        examined += count
        if emit:
            emit({'completed_batches': batches, 'completed_month_pairs': updated['next_pair_index'],
                  'reviewed_pair_receipts_this_run': examined, 'provider_calls': 0, 'scores_promoted': 0})
    final, _ = storage.get_content(CHECKPOINT)
    if batches and final != committed:
        raise DateSweepError('private_date_sweep_cursor_changed')
    state = _cursor(final)
    _tail(storage, state)
    ready = _ready(storage)
    if state['next_pair_index'] > ready:
        raise DateSweepError('private_date_sweep_upstream_regressed')
    if state['next_pair_index'] == TARGET:
        reason = 'complete'
    return {'status': 'private_declared_date_sweep', 'stop_reason': reason,
            'completed_batches': batches, 'completed_month_pairs': state['next_pair_index'],
            'total_month_pairs': TARGET, 'reviewed_pair_receipts_this_run': examined,
            'cumulative_pair_receipt_observations': state['totals']['reviewed_pair_receipts'],
            'declared_date_field_observations': state['totals']['declared_date_fields'],
            'dates_near_risk_window': state['totals']['dates_near_risk_window'],
            'dates_outside_risk_window': state['totals']['dates_outside_risk_window'],
            'automatic_resume_ready': batches > 0 and reason in ('batch_limit', 'time_limit')
                and state['next_pair_index'] < ready,
            'provider_calls': 0, **dict.fromkeys(FLAGS, False), 'scores_promoted': 0}


def main():
    from v3_action_daily_budget import write_resume
    write_resume(os.getenv('GITHUB_OUTPUT'), False)
    try:
        if (os.getenv('V3_PRIVATE_ARCHIVE_REPO') != audit.PRIVATE_REPO
            or len(os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', '')) < 20):
            raise DateSweepError('private_date_sweep_credentials_missing')
        storage = QuotaWaitingPrivateGitStorage(audit.PRIVATE_REPO,
            os.environ['V3_PRIVATE_ARCHIVE_TOKEN'], deadline=time.monotonic() + MAX_SECONDS)
        result = sweep(storage, emit=lambda event: print(json.dumps(event, sort_keys=True), flush=True))
        write_resume(os.getenv('GITHUB_OUTPUT'), result['automatic_resume_ready'])
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as error:
        reason = (str(error) if isinstance(error, (DateSweepError, audit.ProximityError,
                  principal.StructuredActionError, supplement.ExtensionError)) else
                  'private_date_sweep_storage_unavailable' if isinstance(error, PrivateHistoryError)
                  else 'private_date_sweep_unavailable')
        print(json.dumps({'status': 'blocked', 'reason': reason, 'source_values_exposed': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
