"""Continue verified PRIVATE supplemental category transactions, with hard budgets.

The existing four-candidate collector controls endpoint routing, immutable reports
and CAS writes. This runner never retries a failed transaction or certifies dates.
"""
from __future__ import annotations

import json
import os
import time

import v3_private_dart_structured_action_extension as source
import v3_action_daily_budget as budget
from v3_dart_continuous_catchup import QuotaWaitingPrivateGitStorage
from v3_krx_private_github_backfill import PrivateHistoryError

MAX_BATCHES = 128
MAX_REQUESTS = 640
MAX_SECONDS = 55 * 60
TARGET_PAIRS = len(source.MONTHS) - 1


class ExtensionContinuationError(ValueError):
    """Fixed codes only, never source values or private identifiers."""


def _position(storage):
    raw, _ = storage.get_content(source.CHECKPOINT)
    state = source._cursor(raw)
    return state['next_pair_index'], state['next_candidate_index']


def _verify_position(storage, expected, previous):
    for attempt in range(4):
        actual = _position(storage)
        if actual == expected:
            return actual
        if actual != previous:
            raise ExtensionContinuationError('supplemental_cursor_changed')
        if attempt < 3:
            time.sleep(.7 * (attempt + 1))
    raise ExtensionContinuationError('supplemental_cursor_readback_lag')


def continuous(storage, fetch, *, max_batches=MAX_BATCHES,
               max_requests=MAX_REQUESTS, max_seconds=MAX_SECONDS,
               clock=None, collector=None, emit=None):
    if (getattr(storage, 'repository', None) != source.PRIVATE_REPO
        or not callable(fetch) or type(max_batches) is not int
        or not 1 <= max_batches <= MAX_BATCHES
        or type(max_requests) is not int
        or not source.MAX_PROVIDER_CALLS <= max_requests <= MAX_REQUESTS
        or type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS):
        raise ExtensionContinuationError('invalid_supplemental_continuation_budget')
    clock = time.monotonic if clock is None else clock
    collector = source.run if collector is None else collector
    if (not callable(clock) or not callable(collector)
        or emit is not None and not callable(emit)):
        raise ExtensionContinuationError('invalid_supplemental_continuation_callback')
    storage.verify_private()
    deadline = clock() + max_seconds
    initial = position = _position(storage)
    requests = batches = candidates = reused = matched = dates = unsupported = 0
    reason = 'batch_limit'

    def counted_fetch(*args):
        nonlocal requests
        if requests >= max_requests:
            raise ExtensionContinuationError('supplemental_provider_budget_reached')
        if clock() >= deadline:
            raise ExtensionContinuationError('supplemental_deadline_reached')
        requests += 1
        return fetch(*args)

    for _ in range(max_batches):
        if clock() >= deadline:
            reason = 'time_limit'
            break
        # Reserve the existing transaction's worst case, including failed calls.
        if max_requests - requests < source.MAX_PROVIDER_CALLS:
            reason = 'provider_budget'
            break
        if _position(storage) != position:
            raise ExtensionContinuationError('supplemental_cursor_changed')
        before = requests
        try:
            report = collector(storage, counted_fetch)
        except ExtensionContinuationError as error:
            if str(error) not in ('supplemental_provider_budget_reached',
                                  'supplemental_deadline_reached'):
                raise
            if _position(storage) != position:
                raise ExtensionContinuationError('supplemental_partial_cursor_changed') from None
            reason = 'time_limit' if str(error) == 'supplemental_deadline_reached' else 'provider_budget'
            break
        if type(report) is not dict:
            raise ExtensionContinuationError('invalid_supplemental_batch_report')
        if report.get('status') == 'waiting_for_private_title_queue':
            if (report.get('provider_calls') != 0 or report.get('processed_candidates') != 0
                or report.get('scores_promoted') != 0 or requests != before
                or _position(storage) != position):
                raise ExtensionContinuationError('invalid_supplemental_waiting_state')
            reason = 'complete' if position == (TARGET_PAIRS, 0) else 'waiting_for_upstream'
            break
        keys = ('provider_calls', 'processed_candidates', 'previous_reports_reused',
                'new_exact_receipt_category_matches', 'reported_schedule_date_candidates_observed',
                'candidates_without_supplemental_endpoint', 'source_pair_candidate_count',
                'source_pair_next_candidate_index', 'source_pair_remaining_candidates', 'next_pair_index')
        if (report.get('status') != 'private_structured_action_supplement_completed'
            or any(type(report.get(k)) is not int or report[k] < 0 for k in keys)
            or report['provider_calls'] != requests - before
            or report['provider_calls'] > source.MAX_PROVIDER_CALLS
            or report['processed_candidates'] > source.MAX_CANDIDATES
            or any(report[k] > report['processed_candidates'] for k in
                   ('previous_reports_reused', 'new_exact_receipt_category_matches',
                    'candidates_without_supplemental_endpoint'))
            or report['source_pair_candidate_count'] > 2000
            or report['source_pair_next_candidate_index'] != position[1] + report['processed_candidates']
            or report['source_pair_next_candidate_index'] + report['source_pair_remaining_candidates']
                != report['source_pair_candidate_count']
            or any(report.get(k) is not False for k in
                   ('complete_event_universe_certified', 'corporate_action_effective_dates_certified',
                    'adjusted_prices_certified'))
            or report.get('scores_promoted') != 0 or report.get('public_issuer_records_written') != 0):
            raise ExtensionContinuationError('untrusted_supplemental_batch_progress')
        expected = ((position[0] + 1, 0) if report['source_pair_remaining_candidates'] == 0
                    else (position[0], report['source_pair_next_candidate_index']))
        if (not position < expected or expected[0] > TARGET_PAIRS
            or report['next_pair_index'] != expected[0]):
            raise ExtensionContinuationError('supplemental_cursor_stalled_or_jumped')
        position = _verify_position(storage, expected, position)
        batches += 1
        candidates += report['processed_candidates']
        reused += report['previous_reports_reused']
        matched += report['new_exact_receipt_category_matches']
        dates += report['reported_schedule_date_candidates_observed']
        unsupported += report['candidates_without_supplemental_endpoint']
        if emit is not None:
            emit({'completed_batches': batches, 'provider_requests': requests,
                  'processed_candidates': candidates, 'completed_month_pairs': position[0],
                  'total_month_pairs': TARGET_PAIRS, 'exact_receipt_categories_matched': matched,
                  'effective_dates_certified': False, 'scores_promoted': 0})
        if position == (TARGET_PAIRS, 0):
            reason = 'complete'
            break
    return {'status': 'private_supplemental_action_continuation', 'stop_reason': reason,
            'completed_batches': batches, 'provider_requests': requests,
            'provider_request_limit': max_requests, 'processed_candidates': candidates,
            'previous_reports_reused': reused, 'exact_receipt_categories_matched': matched,
            'reported_schedule_date_candidates_observed': dates,
            'candidates_without_supplemental_endpoint': unsupported,
            'starting_completed_month_pairs': initial[0], 'completed_month_pairs': position[0],
            'next_candidate_index': position[1], 'total_month_pairs': TARGET_PAIRS,
            'complete_event_universe_certified': False, 'effective_dates_certified': False,
            'adjusted_prices_certified': False, 'scores_promoted': 0,
            'original_documents_requested': 0, 'public_issuer_records_written': 0}


def main():
    try:
        budget.write_resume(os.getenv('GITHUB_OUTPUT'), False)
        if (os.getenv('V3_PRIVATE_ARCHIVE_REPO') != source.PRIVATE_REPO
            or len(os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', '')) < 20
            or len(os.getenv('DART_API_KEY', '')) != 40):
            raise ExtensionContinuationError('supplemental_continuation_credentials_missing')
        batches = int(os.getenv('V3_EXTENSION_CONTINUATION_BATCHES', str(MAX_BATCHES)))
        if not 1 <= batches <= MAX_BATCHES:
            raise ExtensionContinuationError('invalid_supplemental_continuation_budget')
        day = budget.utc_day()
        duration = budget.runtime_seconds(day)
        if duration == 0:
            print(json.dumps({'status': 'waiting_for_next_action_budget_day', 'provider_requests': 0}))
            return 0
        storage = QuotaWaitingPrivateGitStorage(source.PRIVATE_REPO,
            os.environ['V3_PRIVATE_ARCHIVE_TOKEN'], deadline=time.monotonic() + duration)
        ticket = budget.reserve(storage, 'supplemental', min(MAX_REQUESTS, batches * source.MAX_PROVIDER_CALLS), day=day)
        if ticket is None:
            print(json.dumps({'status': 'waiting_for_daily_action_budget', 'provider_requests': 0}))
            return 0
        duration = budget.runtime_seconds(day)
        if duration == 0:
            budget.finish(storage, ticket, 0)
            print(json.dumps({'status': 'waiting_for_next_action_budget_day', 'provider_requests': 0}))
            return 0
        key = os.environ['DART_API_KEY']
        result = continuous(storage,
            lambda ep, corp, start, end: source.official_fetch(key, ep, corp, start, end),
            max_batches=batches, max_requests=ticket['reserved'], max_seconds=duration,
            emit=lambda event: print(json.dumps(event, sort_keys=True), flush=True))
        remaining = budget.finish(storage, ticket, result['provider_requests'])
        resume = budget.resumable(result, remaining, source.MAX_PROVIDER_CALLS)
        result['daily_provider_budget_remaining'] = remaining
        result['automatic_resume_ready'] = resume
        budget.write_resume(os.getenv('GITHUB_OUTPUT'), resume)
        print(json.dumps(result, sort_keys=True), flush=True)
        if os.getenv('GITHUB_STEP_SUMMARY'):
            lines = ['### 추가 공시 분류 공식 대조',
                '이번 실행 처리 후보: ' + str(result['processed_candidates']),
                '공식 조회 시도: ' + str(result['provider_requests']) + '/' + str(result['provider_request_limit']),
                '완료 월 구간: ' + str(result['completed_month_pairs']) + '/' + str(TARGET_PAIRS),
                '공시 종류 일치: ' + str(result['exact_receipt_categories_matched']),
                '실제 효력일·수정주가·투자점수 인증은 별도 검증입니다.']
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as handle:
                handle.write('\n\n'.join(lines) + '\n')
        return 0
    except Exception as error:
        reason = (str(error) if isinstance(error, (ExtensionContinuationError,
                  source.ExtensionError, source.StructuredActionError, budget.DailyBudgetError)) else
                  'supplemental_private_storage_unavailable' if isinstance(error, PrivateHistoryError)
                  else 'supplemental_continuation_unavailable')
        print(json.dumps({'status': 'blocked', 'reason': reason, 'source_values_exposed': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
