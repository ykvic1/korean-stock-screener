"""Bounded continuation of PRIVATE exact-receipt corporate-action JSON audits.

Reuse the existing two-candidate/six-attempt transaction unchanged. No original
ZIP, issuer output, effect-date certification, price adjustment or score write.
"""
from __future__ import annotations

import json
import os
import time

import v3_private_dart_structured_action_receipts as source
import v3_action_daily_budget as budget
from v3_dart_continuous_catchup import QuotaWaitingPrivateGitStorage
from v3_krx_private_github_backfill import PrivateHistoryError, storage_diagnostic

MAX_BATCHES=256
MAX_REQUESTS=MAX_BATCHES*source.MAX_CALLS
MAX_SECONDS=55*60
TARGET_PAIRS=len(source.MONTHS)-1


class ActionContinuationError(ValueError):
    """Fixed codes; never provider bodies, private paths or identities."""


def _position(storage):
    raw,_=storage.get_content(source.CHECKPOINT)
    state=source._cursor(raw)
    return state['next_pair_index'],state['next_candidate_index']


def _read_position_expected(storage,expected,stale):
    """Accept short read lag, never a different/concurrent cursor."""
    for attempt in range(4):
        actual=_position(storage)
        if actual==expected:return actual
        if actual!=stale:
            raise ActionContinuationError('structured_action_cursor_changed_between_batches')
        if attempt<3:time.sleep(.7*(attempt+1))
    raise ActionContinuationError('structured_action_cursor_readback_lag_exhausted')


def continuous(storage,fetch,*,max_batches=MAX_BATCHES,
               max_requests=MAX_REQUESTS,max_seconds=MAX_SECONDS,
               clock=None,collector=None,emit=None,stage_hook=None):
    if (getattr(storage,'repository',None)!=source.PRIVATE_REPO or not callable(fetch)
        or type(max_batches) is not int or not 1<=max_batches<=MAX_BATCHES
        or type(max_requests) is not int or not source.MAX_CALLS<=max_requests<=MAX_REQUESTS
        or type(max_seconds) is not int or not 1<=max_seconds<=MAX_SECONDS):
        raise ActionContinuationError('invalid_structured_action_continuation_budget')
    clock=time.monotonic if clock is None else clock
    collector=source.run if collector is None else collector
    if not callable(clock) or not callable(collector) or (emit is not None and not callable(emit)):
        raise ActionContinuationError('invalid_structured_action_continuation_callback')
    storage.verify_private()
    start=clock();deadline=start+max_seconds
    initial=position=_position(storage)
    requests=batches=candidates=reused=matched=dates=0
    reason='batch_limit'
    def counted_fetch(*args):
        nonlocal requests
        if requests>=max_requests:
            raise ActionContinuationError('structured_action_provider_budget_reached')
        if clock()>=deadline:
            raise ActionContinuationError('structured_action_deadline_reached')
        requests+=1  # Count attempts, including a failed source read.
        return fetch(*args)
    for _ in range(max_batches):
        if clock()>=deadline:
            reason='time_limit';break
        # Reserve a whole existing transaction. No partial candidate is forced
        # just to consume the last few calls of a run's budget.
        if max_requests-requests<source.MAX_CALLS:
            reason='provider_budget';break
        if _position(storage)!=position:
            raise ActionContinuationError('structured_action_cursor_changed_between_batches')
        prior_requests=requests
        try:
            report=collector(storage,counted_fetch,stage_hook=stage_hook)
        except ActionContinuationError as error:
            if str(error) not in ('structured_action_provider_budget_reached','structured_action_deadline_reached'):
                raise
            # The existing transaction can leave a verified orphan report.
            # Its checkpoint must remain unchanged; reuse it next run without
            # refetching its official response or claiming it was committed.
            if _position(storage)!=position:
                raise ActionContinuationError('structured_action_partial_cursor_changed') from None
            reason='time_limit' if str(error)=='structured_action_deadline_reached' else 'provider_budget'
            break
        if type(report) is not dict:
            raise ActionContinuationError('invalid_structured_action_batch_report')
        status=report.get('status')
        if status=='waiting_for_private_title_shortlist':
            if (report.get('provider_calls')!=0 or report.get('processed_candidates')!=0
                or report.get('scores_promoted')!=0 or requests!=prior_requests or _position(storage)!=position):
                raise ActionContinuationError('invalid_structured_action_waiting_state')
            reason='complete' if position==(TARGET_PAIRS,0) else 'waiting_for_upstream'
            break
        counts={k:report.get(k) for k in ('provider_calls','processed_candidates',
            'previously_saved_reports_reused','exact_receipt_categories_matched',
            'reported_schedule_date_candidates_observed')}
        if (status!='private_structured_action_receipt_research'
            or any(type(v) is not int or v<0 for v in counts.values())
            or counts['provider_calls']!=requests-prior_requests
            or counts['provider_calls']>source.MAX_CALLS
            or counts['processed_candidates']>source.MAX_CANDIDATES
            or counts['previously_saved_reports_reused']>counts['processed_candidates']
            or counts['exact_receipt_categories_matched']>counts['processed_candidates']
            or counts['reported_schedule_date_candidates_observed']>10
            or report.get('corporate_action_effective_dates_certified') is not False
            or report.get('adjusted_prices_certified') is not False
            or report.get('scores_promoted')!=0 or report.get('public_issuer_records_written')!=0
            or type(report.get('next_pair_index')) is not int
            or report['next_pair_index'] not in (position[0],position[0]+1)
            or report['next_pair_index']>TARGET_PAIRS):
            raise ActionContinuationError('untrusted_structured_action_batch_progress')
        next_pair=report['next_pair_index']
        expected=(next_pair,position[1]+counts['processed_candidates'] if next_pair==position[0] else 0)
        if not position<expected:
            raise ActionContinuationError('structured_action_cursor_stalled')
        current=_read_position_expected(storage,expected,position)
        position=current;batches+=1
        candidates+=counts['processed_candidates'];reused+=counts['previously_saved_reports_reused']
        matched+=counts['exact_receipt_categories_matched'];dates+=counts['reported_schedule_date_candidates_observed']
        if emit is not None:
            emit({'completed_batches':batches,'provider_requests':requests,
                  'processed_candidates':candidates,'completed_month_pairs':position[0],
                  'total_month_pairs':TARGET_PAIRS,'exact_receipt_categories_matched':matched,
                  'effective_dates_certified':False,'scores_promoted':0})
        if position==(TARGET_PAIRS,0):
            reason='complete';break
    return {'status':'private_structured_action_continuation',
        'stop_reason':reason,'completed_batches':batches,'provider_requests':requests,
        'provider_request_limit':max_requests,'processed_candidates':candidates,
        'previously_saved_reports_reused':reused,'exact_receipt_categories_matched':matched,
        'reported_schedule_date_candidates_observed':dates,
        'starting_completed_month_pairs':initial[0],'completed_month_pairs':position[0],
        'next_candidate_index':position[1],'total_month_pairs':TARGET_PAIRS,
        'effective_dates_certified':False,'adjusted_prices_certified':False,
        'scores_promoted':0,'original_documents_requested':0,'public_issuer_records_written':0}


def main():
    budget.write_resume(os.getenv('GITHUB_OUTPUT'),False)
    if (os.getenv('V3_PRIVATE_ARCHIVE_REPO')!=source.PRIVATE_REPO
        or len(os.getenv('V3_PRIVATE_ARCHIVE_TOKEN',''))<20
        or len(os.getenv('DART_API_KEY',''))!=40):
        raise ActionContinuationError('private_structured_action_credentials_missing')
    max_batches=int(os.getenv('V3_ACTION_CONTINUATION_BATCHES',str(MAX_BATCHES)))
    if not 1<=max_batches<=MAX_BATCHES:
        raise ActionContinuationError('invalid_structured_action_continuation_budget')
    day=budget.utc_day()
    duration=budget.runtime_seconds(day)
    if duration==0:
        print(json.dumps({'status':'waiting_for_next_action_budget_day','provider_requests':0}))
        return
    phase=['private_archive_init']
    store=QuotaWaitingPrivateGitStorage(source.PRIVATE_REPO,os.environ['V3_PRIVATE_ARCHIVE_TOKEN'],
                                       deadline=time.monotonic()+duration)
    ticket=budget.reserve(store,'principal',max_batches*source.MAX_CALLS,day=day)
    if ticket is None:
        print(json.dumps({'status':'waiting_for_daily_action_budget','provider_requests':0}))
        return
    duration=budget.runtime_seconds(day)
    if duration==0:
        budget.finish(store,ticket,0)
        print(json.dumps({'status':'waiting_for_next_action_budget_day','provider_requests':0}))
        return
    key=os.environ['DART_API_KEY']
    result=continuous(store,lambda ep,corp,start,end:source.official_fetch(key,ep,corp,start,end),
        max_batches=max_batches,max_requests=ticket['reserved'],max_seconds=duration,
        emit=lambda event:print(json.dumps(event,sort_keys=True),flush=True),
        stage_hook=lambda name:phase.__setitem__(0,name))
    remaining=budget.finish(store,ticket,result['provider_requests'])
    resume=budget.resumable(result,remaining,source.MAX_CALLS)
    result['daily_provider_budget_remaining']=remaining
    result['automatic_resume_ready']=resume
    budget.write_resume(os.getenv('GITHUB_OUTPUT'),resume)
    print(json.dumps(result,sort_keys=True))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        lines=['### 기업행위 공시 접수번호 공식 대조',
            '이번 실행 처리 후보: '+str(result['processed_candidates']),
            '이번 실행 공식 조회: '+str(result['provider_requests'])+'/'+str(result['provider_request_limit']),
            '완료 월 구간: '+str(result['completed_month_pairs'])+'/'+str(TARGET_PAIRS),
            '접수번호와 공시 종류 일치: '+str(result['exact_receipt_categories_matched']),
            '공시 종류 대조 결과이며 실제 효력일·수정주가 인증은 별도 검증입니다.',
            '종목 점수 변경: 0']
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as handle:handle.write('\n\n'.join(lines)+'\n')


if __name__=='__main__':
    try:main()
    except Exception as error:
        reason=str(error) if isinstance(error,(ActionContinuationError,source.StructuredActionError,budget.DailyBudgetError)) else (
            'private_storage_unavailable' if isinstance(error,PrivateHistoryError)
            else 'structured_action_continuation_unavailable')
        print(json.dumps({'status':'blocked','reason':reason,**(storage_diagnostic(error) if isinstance(error,PrivateHistoryError) else {}),'provider_source_values_exposed':False},sort_keys=True))
        raise SystemExit(1) from None
