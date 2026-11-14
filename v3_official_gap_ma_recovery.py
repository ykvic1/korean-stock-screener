"""Recover MA, RSI and completed-week patterns after an official gap repair."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from official_probe.clients import KRXClient
from official_probe.validation import _numeric
from v3_historical_close_source_probe import DATES, KST, YAHOO, yahoo_closes
from v3_krx_daily_anomaly_triage import PRICE_FIELDS, classify_row
from v3_krx_history_collection import MARKETS, _snapshot_market
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_session_candidates import candidate_dates
from v3_krx_saved_close_crosscheck import safe_failure_reason
from v3_ma_slope_transition import observe, valid_observation, weekly_points_from_checked_closes
from v3_pattern_composite_score import _observed, _weekly_trend, compute_candidates
from v3_pattern_first_observations import (DAILY_CLEAN_TAIL, _clean_ohlcv, observe as observe_daily,
                                           observe_completed_weekly, rsi_observation)
from v3_saved_close_ma_recovery import availability

GAP = '2025-09-19'
NEIGHBORS = ('2025-09-18', '2025-09-22')
OUTER_ANCHORS = ('2025-09-17', '2025-09-23')
REPAIR_SESSIONS = (NEIGHBORS[0], GAP, NEIGHBORS[1])
LONG_MA_ANCHORS = ('2023-12-01', '2024-04-26', '2024-11-29')
FIELDS = ('Open', 'High', 'Low', 'Close', 'Volume')
MAX_TARGETS = 350
WEEKLY_PATTERN_WEEKS = 90


def inspect_source_frame(payload, ticker, expected, anchors):
    closes = yahoo_closes(payload, ticker, sorted(anchors))
    if any(closes.get(day) is None for day in anchors):
        return 'alternative_anchor_unavailable', None
    if any(closes[day] != price for day, price in anchors.items()):
        return 'alternative_close_anchor_mismatch', None
    try:
        data = payload['chart']['result'][0]
        days = [datetime.fromtimestamp(t, KST).date().isoformat() for t in data['timestamp']]
        if days[0] not in expected or days != expected[expected.index(days[0]):]:
            return 'alternative_calendar_mismatch', None
        bars = data['indicators']['quote'][0]
        frame = pd.DataFrame({f: bars[f.lower()] for f in FIELDS},
                             index=pd.DatetimeIndex(days), dtype=float)
        return None, frame
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'alternative_invalid_bar_structure', None


def source_frame(payload, ticker, expected, anchors):
    """Return a validated frame, for callers that only need accept or reject."""
    return inspect_source_frame(payload, ticker, expected, anchors)[1]


def repair(frame, official, shares=None):
    """Recover a missing session and exact official corrections in its three-day block."""
    if frame is None or any(day not in frame.index or day not in official
                            for day in REPAIR_SESSIONS):
        return None
    if not frame.loc[GAP].isna().all():
        return None
    corrections = [day for day in NEIGHBORS if frame.at[day, 'Close'] != official[day][3]]
    if corrections:
        # The dates around the block must use the same share basis. Exact
        # outside closes are checked independently before reaching this step.
        span = (*OUTER_ANCHORS, *REPAIR_SESSIONS)
        if (shares is None or any(day not in frame.index or day not in official
                                 or not shares.get(day) for day in span)
                or len({shares[day] for day in span}) != 1
                or any(frame.at[day, 'Close'] != official[day][3] for day in OUTER_ANCHORS)):
            return None
    result = frame.copy()
    for day in (GAP, *corrections):
        result.loc[day, list(FIELDS)] = official[day]
    return result


def snapshots(client, day, targets):
    values = {}
    for market in MARKETS:
        rows, raw = client.daily_market(market, day)
        _snapshot_market(rows, raw, market=market, asof=day)
        for row in rows:
            key = (market, row['ISU_CD'])
            if key not in targets or classify_row(row)[0] != 'valid_numeric_observation':
                continue
            bars = tuple(_numeric(row[f], f) for f in (*PRICE_FIELDS, 'ACC_TRDVOL'))
            shares = row.get('LIST_SHRS')
            shares = str(shares).replace(',', '').strip()
            values[key] = bars, int(shares) if shares.isascii() and shares.isdecimal() else None
    return values


def fetch(ticker, first, last):
    try:
        start = datetime.fromisoformat(first).replace(tzinfo=KST)
        end = datetime.fromisoformat(last).replace(tzinfo=KST) + timedelta(days=1)
        response = requests.get(YAHOO + ticker, params={
            'period1': int(start.timestamp()), 'period2': int(end.timestamp()), 'interval': '1d',
        }, headers={'User-Agent': 'Mozilla/5.0'}, timeout=(5, 12), allow_redirects=False)
        if response.status_code == 200 and 0 < len(response.content) <= 500_000:
            return response.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def weekly_position(frame, asof):
    """Measure the existing 120-week position from completed closing prices."""
    close = frame['Close']
    completed = close.resample('W-FRI').last()
    completed = completed.loc[:asof]
    if len(completed) < 120:
        return None
    weeks = completed.iloc[-120:]
    history = close.loc[weeks.index[0] - pd.Timedelta(days=6):weeks.index[-1]]
    prices = history.to_numpy()
    if (not np.isfinite(prices).all() or (prices <= 0).any()
            or weeks.isna().any()
            or (np.abs(prices[1:] / prices[:-1] - 1) > .5).any()
            or (date.fromisoformat(asof) - weeks.index[-1].date()).days > 6):
        return None
    return {'asof': weeks.index[-1].date().isoformat(),
            'weekly_ma120_gap_pct': round(100 * (weeks.iloc[-1] / weeks.mean() - 1), 4)}


def weekly_rsi(frame, completed_friday):
    """Use at least 90 consecutive completed weeks after any unresolved close."""
    close = frame.loc[:completed_friday, 'Close']
    previous = close.shift()
    invalid = ~np.isfinite(close) | (close <= 0)
    jumps = ((close / previous - 1).abs() > .5) & (previous > 0)
    excluded = np.flatnonzero(invalid | jumps)
    if len(excluded):
        close = close.iloc[excluded[-1] + 1:]
        if not close.empty:
            first_week = close.index[0].to_period('W-FRI')
            close = close.loc[close.index.to_period('W-FRI') > first_week]
    weeks = close.resample('W-FRI').last()
    if (len(weeks) < 90 or weeks.isna().any()
            or weeks.index[-1].date().isoformat() != completed_friday):
        return None
    evidence = rsi_observation(weeks.to_numpy())
    return {'asof': completed_friday, **evidence} if evidence['rsi14'] is not None else None


def needs_weekly_pattern(stock, row):
    return 'weekly_pattern' not in row['parts']['technical'] and _observed(stock, 'weekly') is None


def needs_daily_pattern(stock, row):
    parts = row['parts']['technical']
    return ('daily_pattern' not in parts and 'daily_rsi' not in parts
            and _observed(stock, 'daily') is None)


def recover_daily_pattern(stock, frame, official, day):
    """Check recent complete bars against KRX before replaying the daily detector."""
    tail = frame.iloc[-DAILY_CLEAN_TAIL:]
    anchors = [d for d in official if d in tail.index]
    if (len(tail) < DAILY_CLEAN_TAIL or tail.index[-1].date().isoformat() != day
            or day not in anchors or len(anchors) < 3
            or any(tuple(tail.loc[d, list(FIELDS)]) != official[d] for d in anchors)):
        return None
    bars = _clean_ohlcv(tail)
    if bars is None or (np.abs(bars[1:, 3] / bars[:-1, 3] - 1) > .5).any():
        return None
    candidate = observe_daily(tail)
    candidate['source_recent_clean_window_only'] = len(frame) > len(tail)
    return _observed({**stock, 'pattern_research': candidate}, 'daily')


def recover_weekly_pattern(stock, frame, official, day):
    """Use the existing weekly detector on a completed, cross-checked history."""
    # Match both feeds' volumes around the replacement before aggregating
    # a mixed-source week. This does not certify any venue/session split.
    if any(frame.at[d, 'Volume'] != official[d][4] for d in OUTER_ANCHORS):
        return None
    closes = frame['Close'].to_numpy()
    if (not np.isfinite(closes).all() or (closes <= 0).any()
            or (np.abs(closes[1:] / closes[:-1] - 1) > .5).any()):
        return None
    candidate = observe_completed_weekly(frame)
    if candidate.get('asof') != day:
        return None
    return _observed({**stock, 'pattern_weekly_research': candidate}, 'weekly')


def weekly_pattern_window(expected, completed_friday):
    """Find complete observed-market weeks and spread anchors across the window."""
    by_week = {}
    for session in expected:
        dt = date.fromisoformat(session)
        friday = (dt + timedelta(days=(4 - dt.weekday()) % 7)).isoformat()
        if friday <= completed_friday:
            by_week.setdefault(friday, []).append(session)
    weeks = list(by_week.values())[-WEEKLY_PATTERN_WEEKS:]
    if len(weeks) < WEEKLY_PATTERN_WEEKS:
        raise ValueError('operating_sources_not_aligned')
    anchor_days = [weeks[index][-1] for index in (0, 29, 59, -1)]
    return weeks[0][0], weeks[-1][-1], anchor_days


def recover_recent_weekly_pattern(stock, frame, values, shares, window, friday):
    """Replay the same detector on a clean, independently anchored 90-week suffix."""
    first, last, anchor_days = window
    if frame is None or first not in frame.index or last not in frame.index:
        return None
    tail = frame.loc[first:last]
    if any(d not in values or tuple(tail.loc[d, list(FIELDS)]) != values[d]
           for d in anchor_days):
        return None
    if GAP in tail.index and tail.loc[GAP].isna().all():
        if (any(d not in values or d not in tail.index or
                tuple(tail.loc[d, list(FIELDS)]) != values[d] for d in OUTER_ANCHORS)
                or any(not shares.get(d) for d in (*OUTER_ANCHORS, *REPAIR_SESSIONS))
                or len({shares[d] for d in (*OUTER_ANCHORS, *REPAIR_SESSIONS)}) != 1):
            return None
        tail = repair(tail, values, shares)
        if tail is None:
            return None
    bars = _clean_ohlcv(tail)
    if bars is None or (np.abs(bars[1:, 3] / bars[:-1, 3] - 1) > .5).any():
        return None
    candidate = observe_completed_weekly(tail, completed_through=friday)
    if candidate.get('asof') != friday:
        return None
    candidate['source_recent_clean_window_only'] = True
    return _observed({**stock, 'pattern_weekly_research': candidate}, 'weekly')


def needs_weekly_rsi(stock, row):
    return ('weekly_rsi' not in row['parts']['technical']
            and _observed(stock, 'weekly') is None
            and stock.get('v3_weekly_rsi_gap_recovery') is None)


def needs_weekly_ma120(stock, day, completed_friday):
    receipt = stock.get('v3_ma_slope_transition')
    return (valid_observation(receipt, day)
            and receipt['weekly_asof'] == completed_friday
            and receipt['parts']['weekly']['120'] is None)


def add_weekly_ma120(receipt, close, completed_friday):
    if receipt is None or receipt['weekly_asof'] != completed_friday:
        return receipt
    weekly = receipt['parts']['weekly']
    if weekly is None or weekly['120'] is not None:
        return receipt
    points = weekly_points_from_checked_closes(close, completed_friday)
    if points is None or points['120'] is None:
        return receipt
    return dict(receipt, parts=dict(receipt['parts'], weekly=dict(weekly, **{'120': points['120']})))


def needs_weekly_position(stock, row):
    if 'long_ma_position' in row['parts']['technical']:
        return False
    old = _observed(stock, 'weekly')
    return ((old is None or _weekly_trend(old) is None)
            and stock.get('v3_weekly_position_gap_recovery') is None)


def selected(market, priority, report):
    day = market['date']
    if (day < DATES[-1] or priority['asof'] != day or report['asof'] != day
            or priority['operational_source_digest'] != report['source_digest']):
        raise ValueError('operating_sources_not_aligned')
    focus = set(priority['coverage_90_first']['family_conditional_priority_codes']['price_chart_windows'])
    if len({stock['code'] for stock in market['stocks']}) != len(market['stocks']):
        raise ValueError('duplicate_market_identity')
    result = {}
    for stock in market['stocks']:
        code = stock['code']
        row = report['rows'][code]
        if (stock['price_date'] == day and stock['market'] in MARKETS
            and (code in focus or 90 <= row['available'] < 100)
            and (availability(stock.get('v3_ma_slope_transition'), day) != (2., 3.)
                 or needs_weekly_position(stock, row) or needs_weekly_rsi(stock, row)
                 or needs_daily_pattern(stock, row)
                 or needs_weekly_pattern(stock, row))):
            key = (stock['market'], code)
            result[key] = stock
    if len(result) > MAX_TARGETS:
        raise ValueError('candidate_budget_exceeded')
    return result


def run(market, priority, report, client):
    day = market['date']
    candidates = selected(market, priority, report)
    target_day = date.fromisoformat(day)
    completed_friday = (target_day - timedelta(days=(target_day.weekday() - 4) % 7)).isoformat()
    first = (target_day - timedelta(days=1100)).isoformat()
    expected = candidate_dates(calendar_weekday_candidates(first, day), source='xkrx')['dates']
    if day not in expected:
        raise ValueError('operating_sources_not_aligned')
    completed_close_day = max(d for d in expected if d <= completed_friday)
    close_anchors = sorted(set(DATES) | set(OUTER_ANCHORS) | {completed_close_day, day})
    current = snapshots(client, day, candidates)
    long_ma_targets = {key: stock for key, stock in candidates.items()
                       if key in current and needs_weekly_ma120(stock, day, completed_friday)}
    long_ma_refs = {d: snapshots(client, d, long_ma_targets)
                    for d in LONG_MA_ANCHORS} if long_ma_targets else {}
    dates = sorted(set(close_anchors) | set(REPAIR_SESSIONS))
    targets = {key: stock for key, stock in candidates.items() if key in current and (
        current[key][0][3] != stock['price']
        or needs_weekly_position(stock, report['rows'][key[1]])
        or needs_weekly_rsi(stock, report['rows'][key[1]])
        or needs_daily_pattern(stock, report['rows'][key[1]])
        or needs_weekly_pattern(stock, report['rows'][key[1]])
        or key in long_ma_targets)}
    refs = {day: current}
    for d in dates[:-1]:
        refs[d] = snapshots(client, d, targets)
    recent_week = weekly_pattern_window(expected, completed_friday)
    weekly_targets = {key: stock for key, stock in targets.items()
                      if needs_weekly_pattern(stock, report['rows'][key[1]])}
    extra_refs = {d: snapshots(client, d, weekly_targets)
                  for d in recent_week[2] if d not in refs and weekly_targets}

    def examine(item):
        key, stock = item
        missing = next((d for d in dates if key not in refs[d]), None)
        row = report['rows'][key[1]]
        daily_needed = needs_daily_pattern(stock, row)
        weekly_needed = key in weekly_targets
        if missing is not None and not (daily_needed or weekly_needed):
            return ('official_gap_row_unavailable' if missing in REPAIR_SESSIONS
                    else 'official_anchor_unavailable'), None
        values = {d: refs[d][key][0] for d in dates if key in refs[d]}
        shares = {d: refs[d][key][1] for d in values}
        ticker = key[1] + ('.KS' if key[0] == 'KOSPI' else '.KQ')
        payload = fetch(ticker, first, day)
        if payload is None:
            return 'alternative_response_unavailable', None
        updates = {}
        if daily_needed:
            tail_start = expected[-DAILY_CLEAN_TAIL]
            daily_anchors = {d: v for d, v in values.items() if d >= tail_start}
            if day in daily_anchors and len(daily_anchors) >= 3:
                daily_frame = source_frame(payload, ticker, expected,
                                           {d: v[3] for d, v in daily_anchors.items()})
                daily = (recover_daily_pattern(stock, daily_frame, daily_anchors, day)
                         if daily_frame is not None else None)
                if daily is not None:
                    updates['pattern_research'] = daily
        if weekly_needed:
            weekly_anchors = {d: values.get(d, extra_refs.get(d, {}).get(key, (None, None))[0])
                              for d in recent_week[2]}
            if all(v is not None for v in weekly_anchors.values()):
                week_frame = source_frame(payload, ticker, expected,
                                          {d: v[3] for d, v in weekly_anchors.items()} |
                                          {day: current[key][0][3]})
                weekly = recover_recent_weekly_pattern(stock, week_frame, values | weekly_anchors,
                                                       shares, recent_week, completed_friday)
                if weekly is not None:
                    updates['pattern_weekly_research'] = weekly
        if missing is not None:
            return ('recovered' if updates else 'official_anchor_unavailable'), updates
        reason, frame = inspect_source_frame(payload, ticker, expected,
            {d: values[d][3] for d in close_anchors})
        if reason is not None:
            return ('recovered' if updates else reason), updates
        if GAP not in frame.index or not frame.loc[GAP].isna().all():
            return ('recovered' if updates else 'alternative_gap_not_all_null'), updates
        repaired = repair(frame, values, shares)
        if repaired is None:
            return ('recovered' if updates else 'local_share_basis_or_neighbor_unverified'), updates
        if weekly_needed and 'pattern_weekly_research' not in updates:
            weekly = recover_weekly_pattern(stock, repaired, values, completed_friday)
            if weekly is not None:
                updates['pattern_weekly_research'] = weekly
        if needs_weekly_position(stock, row):
            position = weekly_position(repaired, day)
            if position is not None:
                updates['v3_weekly_position_gap_recovery'] = position
        if needs_weekly_rsi(stock, row):
            rsi_evidence = weekly_rsi(repaired, completed_friday)
            if rsi_evidence is not None:
                updates['v3_weekly_rsi_gap_recovery'] = rsi_evidence
        previous = stock.get('v3_ma_slope_transition')
        old = availability(previous, day)
        if old != (2., 3.):
            receipt = observe(repaired)
            new = availability(receipt, day)
            if (new != old and all(a <= b for a, b in zip(old, new))
                and (previous is None or all(
                    (receipt['parts'][frame] or {}).get(period) is not None
                    for frame, parts in previous['parts'].items()
                    for period, points in (parts or {}).items() if points is not None
                ))):
                updates['v3_ma_slope_transition'] = receipt
        # Weekly MA uses closes only. An unrelated intraday field can make
        # the OHLCV path unavailable even when the dated closes are intact.
        if key in long_ma_targets and all(
            key in historical and d in repaired.index
            and repaired.at[d, 'Close'] == historical[key][0][3]
            for d, historical in long_ma_refs.items()
        ):
            base = updates.get('v3_ma_slope_transition', previous)
            extended = add_weekly_ma120(base, repaired['Close'], completed_friday)
            if extended is not base:
                updates['v3_ma_slope_transition'] = extended
        return ('recovered' if updates else 'no_added_observation'), updates

    counts = Counter()
    targets = dict(sorted(targets.items(), key=lambda item: (
        report['rows'][item[0][1]]['available'] >= 90,
        abs(90-report['rows'][item[0][1]]['available'])
        if report['rows'][item[0][1]]['available'] < 90
        else 100-report['rows'][item[0][1]]['available'],
        item[0][1])))
    recovered = Counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (key, stock), (outcome, updates) in zip(targets.items(), pool.map(examine, targets.items())):
            counts[outcome] += 1
            if updates:
                slope = updates.get('v3_ma_slope_transition')
                if key in long_ma_targets and slope is not None and (
                    slope['parts']['weekly'] or {}
                ).get('120') is not None:
                    recovered['weekly_ma120_transition'] += 1
                stock.update(updates)
                recovered.update(updates.keys())
    return {'asof': day, 'gap_session': GAP, 'comparison_sessions': close_anchors,
            'official_repair_sessions': list(REPAIR_SESSIONS),
            'selected': len(targets), 'counts': dict(counts), 'recovered_fields': dict(recovered),
            'long_ma_comparison_sessions': list(long_ma_refs),
            'weekly_suffix_comparison_sessions': recent_week[2],
            'official_requests': 2*(len(dates) + len(long_ma_refs) + len(extra_refs)),
            'source_history_adjusted_verified': False,
            'volume_score_changed': False, 'raw_source_exported': False}


def main():
    root = Path('docs')
    data = root/'data.json'
    raw = data.read_bytes()
    market = json.loads(raw)

    def load(name):
        return json.loads((root/name).read_text(encoding='utf-8'))

    result = run(market, load('v3-operational-evidence-priority.json'),
                 load('v3-operational-research-scores.json'), KRXClient(os.environ.get('KRX_API_KEY', '')))
    result['input_market_sha256'] = sha256(raw).hexdigest()
    if any(result['recovered_fields'].get(key) for key in ('pattern_research', 'pattern_weekly_research')):
        compute_candidates(market['stocks'])
        status = market['v3_technical_pattern_status']
        status['technical_observed_25_count'] = sum(
            r['v3_pattern_integrated']['technical_research_candidate'] is not None
            for r in market['stocks'])
        status['candidate_full_six_pillar_count'] = sum(
            r['v3_pattern_integrated']['six_pillar_full_candidate_only'] is not None
            for r in market['stocks'])
    if result['recovered_fields']:
        data.write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    (root/'v3-official-gap-ma-recovery.json').write_text(encoded+'\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error) if type(error) is ValueError and str(error) in (
            'operating_sources_not_aligned', 'duplicate_market_identity',
            'candidate_budget_exceeded', 'KRX_API_KEY is required',
        ) else safe_failure_reason(error)
        print(json.dumps({'status': 'blocked', 'reason': reason}))
        raise SystemExit(1)
