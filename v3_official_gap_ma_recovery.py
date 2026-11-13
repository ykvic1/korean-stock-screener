"""Recover price-only MA observations for the dated Yahoo daily source gap."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path

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
from v3_ma_slope_transition import observe
from v3_saved_close_ma_recovery import availability

GAP = '2025-09-19'
NEIGHBORS = ('2025-09-18', '2025-09-22')
OUTER_ANCHORS = ('2025-09-17', '2025-09-23')
REPAIR_SESSIONS = (NEIGHBORS[0], GAP, NEIGHBORS[1])
FIELDS = ('Open', 'High', 'Low', 'Close', 'Volume')
MAX_TARGETS = 350


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


def selected(market, priority, report):
    day = market['date']
    if (day != DATES[-1] or priority['asof'] != day or report['asof'] != day
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
            and availability(stock.get('v3_ma_slope_transition'), day) != (2., 3.)):
            key = (stock['market'], code)
            result[key] = stock
    if len(result) > MAX_TARGETS:
        raise ValueError('candidate_budget_exceeded')
    return result


def run(market, priority, report, client):
    day = market['date']
    candidates = selected(market, priority, report)
    dates = sorted([*DATES, *REPAIR_SESSIONS, *OUTER_ANCHORS])
    current = snapshots(client, day, candidates)
    targets = {key: stock for key, stock in candidates.items()
               if key in current and current[key][0][3] != stock['price']}
    refs = {day: current}
    for d in dates[:-1]:
        refs[d] = snapshots(client, d, targets)
    first = (date.fromisoformat(day) - timedelta(days=1100)).isoformat()
    expected = candidate_dates(calendar_weekday_candidates(first, day), source='xkrx')['dates']

    def examine(item):
        key, stock = item
        if any(key not in refs[d] for d in dates):
            missing = next(d for d in dates if key not in refs[d])
            return ('official_gap_row_unavailable' if missing in REPAIR_SESSIONS
                    else 'official_anchor_unavailable'), None
        values = {d: refs[d][key][0] for d in dates}
        shares = {d: refs[d][key][1] for d in dates}
        ticker = key[1] + ('.KS' if key[0] == 'KOSPI' else '.KQ')
        payload = fetch(ticker, first, day)
        if payload is None:
            return 'alternative_response_unavailable', None
        reason, frame = inspect_source_frame(payload, ticker, expected,
            {d: values[d][3] for d in (*DATES, *OUTER_ANCHORS)})
        if reason is not None:
            return reason, None
        if GAP not in frame.index or not frame.loc[GAP].isna().all():
            return 'alternative_gap_not_all_null', None
        repaired = repair(frame, values, shares)
        if repaired is None:
            return 'local_share_basis_or_neighbor_unverified', None
        receipt = observe(repaired)
        old = availability(stock.get('v3_ma_slope_transition'), day)
        new = availability(receipt, day)
        previous = stock.get('v3_ma_slope_transition')
        if old == new or any(a > b for a, b in zip(old, new)):
            return 'no_added_ma_weight', None
        if previous is not None and any(
            (receipt['parts'][frame] or {}).get(period) is None
            for frame, parts in previous['parts'].items()
            for period, points in (parts or {}).items() if points is not None
        ):
            return 'previous_ma_window_unavailable', None
        return 'recovered_ma_weight', receipt

    counts = Counter()
    targets = dict(sorted(targets.items(), key=lambda item: (
        report['rows'][item[0][1]]['available'] >= 90,
        abs(90-report['rows'][item[0][1]]['available'])
        if report['rows'][item[0][1]]['available'] < 90
        else 100-report['rows'][item[0][1]]['available'],
        item[0][1])))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for stock, (outcome, receipt) in zip(targets.values(), pool.map(examine, targets.items())):
            counts[outcome] += 1
            if receipt is not None:
                stock['v3_ma_slope_transition'] = receipt
    return {'asof': day, 'gap_session': GAP, 'comparison_sessions': sorted([*DATES, *OUTER_ANCHORS]),
            'official_repair_sessions': list(REPAIR_SESSIONS),
            'selected': len(targets), 'counts': dict(counts),
            'official_requests': 2*len(dates), 'source_history_adjusted_verified': False,
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
    if result['counts'].get('recovered_ma_weight'):
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
