"""Private ISO-calendar-week ACC_TRDVAL aggregates, not five-day proxies.

No response bytes, daily amounts or closing prices leave the derivation.
Only quantized weekly totals and coverage are stored privately. A pinned
candidate calendar groups holidays correctly; independent market/session
and corporate-action certification are NOT asserted.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, time as daytime
from hashlib import sha256
from io import BytesIO
import gzip
import json
import os
import re
import time
from zoneinfo import ZoneInfo

from official_probe.clients import KRXClient, KRXProbeError
from v3_krx_history_collection import _snapshot_market
from v3_krx_money_5day import _row_values
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_session_candidates import candidate_dates
from v3_krx_private_github_backfill import PrivateHistoryError
from v3_dart_continuous_catchup import QuotaWaitingPrivateGitStorage
from v3_dart_historical_event_receipts import _save_cursor

SCHEMA = 'v3-private-krx-calendar-week-money-v1'
CURSOR_SCHEMA = 'v3-private-krx-calendar-week-money-cursor-v1'
CHECKPOINT = 'derived/money-calendar-week-checkpoint.json'
PREFIX = 'derived/money-calendar-weeks'
PRIVATE_REPO = 'ykvic1/korean-stock-private-data'
HISTORY_WEEKS = 240  # 120 completed weeks plus an equally long disjoint baseline.
MAX_REQUESTS = 3000
MAX_FILE_BYTES = 800_000
MAX_TOTAL_BYTES = 100 * 1024 * 1024


class WeeklyMoneyError(ValueError):
    """Static diagnostics only."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def closed_week_end(asof):
    d = date.fromisoformat(asof)
    if d.isoformat() != asof:
        raise WeeklyMoneyError('invalid_weekly_money_cutoff')
    # Friday EOD (and the weekend) completes the market week. Sunday is
    # the ISO grouping label, not an observation from a future trading day.
    return (d + timedelta(days=6-d.weekday()) if d.weekday()>=4
            else d-timedelta(days=d.weekday()+1)).isoformat()


def week_days(week_end):
    end = date.fromisoformat(week_end)
    if end.weekday() != 6:
        raise WeeklyMoneyError('invalid_money_calendar_week_boundary')
    start = end-timedelta(days=6)
    days = candidate_dates(calendar_weekday_candidates(start.isoformat(), end.isoformat()), source='xkrx')['dates']
    if not 1 <= len(days) <= 5 or any(date.fromisoformat(d).isocalendar()[:2] != start.isocalendar()[:2] for d in days):
        raise WeeklyMoneyError('unavailable_money_calendar_week_sessions')
    return days


def path(week_end):
    return PREFIX+'/'+week_end+'.json.gz'


def check_week(week_end, days):
    end=date.fromisoformat(week_end)
    if (end.weekday()!=6 or type(days) is not list or not 1<=len(days)<=5
        or days!=sorted(set(days)) or any(
            not end-timedelta(days=6)<=date.fromisoformat(d)<=end
            or date.fromisoformat(d).weekday()>4 for d in days)):
        raise WeeklyMoneyError('invalid_money_week_source_dates')
    return end


def derive(client, week_end, days, budget):
    end = check_week(week_end,days)
    amounts = {'KOSPI': {}, 'KOSDAQ': {}}
    for day in days:
        for market in amounts:
            for attempt in range(3):
                if budget['requests'] >= MAX_REQUESTS:
                    raise WeeklyMoneyError('weekly_money_request_budget_reached')
                budget['requests'] += 1
                try:
                    rows, raw = client.daily_market(market, day)
                    break
                except KRXProbeError as error:
                    if attempt == 2 or error.reason not in ('empty_body', 'empty_market_rows', 'transport_failure', 'http_server_error'):
                        issue=WeeklyMoneyError('official_weekly_money_source_unavailable')
                        issue.safe_krx_reason=error.reason
                        issue.safe_krx_http_status=error.http_status
                        issue.safe_source_day=day
                        issue.safe_source_market=market
                        raise issue from None
                    time.sleep(attempt+1)
            _snapshot_market(rows, raw, market=market, asof=day)
            usable = 0
            for row in rows:
                code = row.get('ISU_CD')
                if type(code) is not str or not re.fullmatch('[0-9A-Z]{6}', code):
                    raise WeeklyMoneyError('invalid_money_week_issuer_identity')
                value = _row_values(row)
                if value is not None:
                    amounts[market].setdefault(code, []).append(value[1])
                    usable += 1
            if not usable:
                raise WeeklyMoneyError('missing_actual_weekly_trading_value')
            del rows, raw
    factors = {}
    for market, issuers in amounts.items():
        for code, values in issuers.items():
            if len(values) == len(days):
                factors[market+':'+code] = {'amount_100k_krw': round(sum(values)/100_000),
                                          'observed_sessions': len(days)}
    data = {'schema': SCHEMA, 'week_start': (end-timedelta(days=6)).isoformat(),
            'week_end': week_end, 'session_end': days[-1], 'session_dates': days,
            'session_calendar_sha256': sha256(canonical(days)).hexdigest(),
            'issuer_week_factors': factors, 'amount_bucket_krw': 100_000,
            'direct_acc_trdval': True, 'calendar_week_grouping_verified': True,
            'independent_regular_session_verified': False, 'corporate_actions_verified': False,
            'daily_amount_or_price_bars_retained': False, 'investment_score_certified': False}
    blob = gzip.compress(canonical(data), compresslevel=9, mtime=0)
    if not 0 < len(blob) < MAX_FILE_BYTES:
        raise WeeklyMoneyError('weekly_money_payload_budget_exceeded')
    return data, blob


def validate(blob, week_end, days):
    try:
        if type(blob) is not bytes or not 0 < len(blob) < MAX_FILE_BYTES:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as handle:
            raw=handle.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError()
        d = json.loads(raw)
        end = check_week(week_end,days)
        expected={'schema','week_start','week_end','session_end','session_dates',
                  'session_calendar_sha256','issuer_week_factors','amount_bucket_krw',
                  'direct_acc_trdval','calendar_week_grouping_verified',
                  'independent_regular_session_verified','corporate_actions_verified',
                  'daily_amount_or_price_bars_retained','investment_score_certified'}
        if (set(d)!=expected or d.get('schema') != SCHEMA
            or d.get('week_start') != (end-timedelta(days=6)).isoformat()
            or d.get('week_end') != week_end or d.get('session_dates') != days
            or d.get('session_end') != days[-1] or d.get('amount_bucket_krw') != 100_000
            or d.get('session_calendar_sha256') != sha256(canonical(days)).hexdigest()
            or d.get('direct_acc_trdval') is not True or d.get('calendar_week_grouping_verified') is not True
            or any(d.get(k) is not False for k in ('independent_regular_session_verified',
                   'corporate_actions_verified','daily_amount_or_price_bars_retained','investment_score_certified'))
            or not isinstance(d.get('issuer_week_factors'), dict)):
            raise ValueError()
        for key, item in d['issuer_week_factors'].items():
            if (not re.fullmatch('(KOSPI|KOSDAQ):[0-9A-Z]{6}', key)
                or type(item) is not dict or set(item) != {'amount_100k_krw','observed_sessions'}
                or type(item['amount_100k_krw']) is not int or not 0<=item['amount_100k_krw']<5*10**12
                or type(item['observed_sessions']) is not int or item['observed_sessions'] != len(days)):
                raise ValueError()
        return d
    except Exception:
        raise WeeklyMoneyError('invalid_private_calendar_week_money') from None


def manifest(raw, asof):
    latest = date.fromisoformat(closed_week_end(asof))
    if raw is None:
        return {'schema': CURSOR_SCHEMA, 'anchor_week_end': latest.isoformat(),
                'history_completed': 0, 'forward_completed': 0, 'compressed_bytes': 0,
                'tail_week_end': None, 'tail_sha256': None}
    try:
        d = json.loads(raw)
        anchor = date.fromisoformat(d['anchor_week_end'])
        if (d.get('schema') != CURSOR_SCHEMA or anchor.weekday() != 6 or anchor > latest
            or type(d['history_completed']) is not int or not 0 <= d['history_completed'] <= HISTORY_WEEKS
            or type(d['forward_completed']) is not int
            or not 0 <= d['forward_completed'] <= (latest-anchor).days//7
            or (d['forward_completed']>0 and d['history_completed']!=HISTORY_WEEKS)
            or type(d['compressed_bytes']) is not int or not 0 <= d['compressed_bytes'] <= MAX_TOTAL_BYTES):
            raise ValueError()
        expected = (anchor+timedelta(weeks=d['forward_completed'])).isoformat() if d['forward_completed'] else (
            (anchor-timedelta(weeks=d['history_completed']-1)).isoformat() if d['history_completed'] else None)
        if (d['tail_week_end'] != expected or
            (expected is None and d['tail_sha256'] is not None) or
            (expected is not None and not re.fullmatch('[a-f0-9]{64}',d['tail_sha256']))):
            raise ValueError()
        return d
    except Exception:
        raise WeeklyMoneyError('invalid_weekly_money_checkpoint') from None


def collect(store, client, *, asof, max_weeks=HISTORY_WEEKS, day_loader=week_days):
    if store.repository != PRIVATE_REPO or type(max_weeks) is not int or not 1 <= max_weeks <= HISTORY_WEEKS:
        raise WeeklyMoneyError('invalid_weekly_money_collection_request')
    store.verify_private()
    raw, oldsha = store.get_content(CHECKPOINT)
    state = manifest(raw, asof)
    if state['tail_week_end']:
        tail,_=store.get_content(path(state['tail_week_end']))
        if tail is None or sha256(tail).hexdigest()!=state['tail_sha256']:
            raise WeeklyMoneyError('weekly_money_checkpoint_tail_changed')
        validate(tail,state['tail_week_end'],day_loader(state['tail_week_end']))
    budget={'requests':0}
    stored=0
    for _ in range(max_weeks):
        anchor=date.fromisoformat(state['anchor_week_end'])
        if state['history_completed'] < HISTORY_WEEKS:
            end=(anchor-timedelta(weeks=state['history_completed'])).isoformat()
            field='history_completed'
        elif anchor+timedelta(weeks=state['forward_completed']) < date.fromisoformat(closed_week_end(asof)):
            end=(anchor+timedelta(weeks=state['forward_completed']+1)).isoformat()
            field='forward_completed'
        else:
            break
        days=day_loader(end)
        blob,_=store.get_content(path(end))
        if blob is None:
            _,blob=derive(client,end,days,budget)
            try:
                store.put_new(path(end),blob,'Save private calendar-week actual-money aggregate')
            except PrivateHistoryError as err:
                if getattr(err,'safe_http_status',None)!='http_409':
                    raise
                observed,_=store.get_content(path(end))
                if observed is None:
                    store.put_new(path(end),blob,'Save private calendar-week actual-money aggregate')
                elif observed!=blob:
                    raise WeeklyMoneyError('conflicting_private_money_week') from None
            observed,_=store.get_content(path(end))
            if observed!=blob:
                raise WeeklyMoneyError('private_money_week_readback_failed')
        validate(blob,end,days)
        updated={**state,field:state[field]+1,'tail_week_end':end,
                 'tail_sha256':sha256(blob).hexdigest(),
                 'compressed_bytes':state['compressed_bytes']+len(blob)}
        if updated['compressed_bytes']>MAX_TOTAL_BYTES:
            raise WeeklyMoneyError('weekly_money_archive_budget_exceeded')
        encoded=canonical(updated)
        oldsha=_save_cursor(store,encoded,raw,oldsha,checkpoint=CHECKPOINT)
        raw,state=encoded,updated
        stored+=1
        print(json.dumps({'weekly_history_completed':state['history_completed'],
                          'weekly_forward_completed':state['forward_completed'],
                          'source_requests_this_run':budget['requests']}),flush=True)
    return {'history_completed':state['history_completed'],'history_target':HISTORY_WEEKS,
            'forward_completed':state['forward_completed'],'source_requests':budget['requests'],
            'weeks_saved_or_resumed':stored,'raw_source_files_saved':0}


def main():
    key=os.getenv('KRX_API_KEY','')
    if (not key or os.getenv('V3_PRIVATE_ARCHIVE_REPO')!=PRIVATE_REPO
        or len(os.getenv('V3_PRIVATE_ARCHIVE_TOKEN',''))<20):
        raise WeeklyMoneyError('missing_private_week_money_credentials')
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    cutoff=now.date() if now.time()>=daytime(18) else now.date()-timedelta(days=1)
    store=QuotaWaitingPrivateGitStorage(PRIVATE_REPO,os.environ['V3_PRIVATE_ARCHIVE_TOKEN'],
                                      deadline=time.monotonic()+100*60)
    result=collect(store,KRXClient(key),asof=cutoff.isoformat())
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        result={'status':'blocked','category':str(error) if isinstance(error,WeeklyMoneyError)
                else 'private_calendar_week_money_not_verified','source_payload_logged':False}
        reason=getattr(error,'safe_krx_reason',None)
        if reason in KRXProbeError.REASONS:
            result['krx_allowlisted_failure_reason']=reason
            result['http_status_only']=getattr(error,'safe_krx_http_status',None)
            day=getattr(error,'safe_source_day',None)
            market=getattr(error,'safe_source_market',None)
            if type(day) is str and re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',day):
                result['source_day']=day
            if market in ('KOSPI','KOSDAQ'):result['source_market']=market
        if isinstance(error,PrivateHistoryError):
            from v3_krx_money_backfill import _safe_private_failure_diagnostics
            result['private_storage']=_safe_private_failure_diagnostics(error)
        print(json.dumps(result,sort_keys=True))
        raise SystemExit(1) from None
