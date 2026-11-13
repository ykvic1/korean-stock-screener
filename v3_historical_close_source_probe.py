"""Crosscheck alternative historical closes against exact-date KRX OpenAPI rows.

The same priority sample is used for every date. Only counts leave the process.
No OHLCV series, issuer identifiers, adjusted-price claims or scores are saved.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from official_probe.clients import KRXClient
from v3_dated_market_roster import load_dated_listing
from v3_fdr_cache_krx_close_corroboration import _price, cache_close_index
from v3_krx_daily_anomaly_triage import classify_row
from v3_krx_history_collection import MARKETS, _snapshot_market, validate_dates
from v3_krx_saved_close_crosscheck import safe_failure_reason

KST = ZoneInfo('Asia/Seoul')
DATES = ['2026-09-14', '2026-10-19', '2026-10-30', '2026-11-02', '2026-11-12']
MAX_SAMPLE = 24
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/'
OUT = 'v3-historical-close-source-probe.json'
SCHEMA = 'v3-historical-close-source-probe-v1'
OUTCOMES = ('exact_close', 'different_close', 'source_unavailable', 'official_unavailable')


class CloseSourceError(ValueError):
    """Fixed reason label."""


def yahoo_closes(payload, ticker, days):
    """Accept dated KRW common-equity close values with an exact response identity."""
    try:
        chart = payload['chart']
        results = chart['result']
        if chart['error'] is not None or len(results) != 1:
            return {}
        result = results[0]
        meta = result['meta']
        if (meta['symbol'] != ticker or meta['currency'] != 'KRW'
                or meta['exchangeTimezoneName'] != 'Asia/Seoul'
                or meta['instrumentType'] != 'EQUITY'):
            return {}
        times = result['timestamp']
        quotes = result['indicators']['quote']
        if len(quotes) != 1:
            return {}
        close = quotes[0]['close']
        if not 1 <= len(times) == len(close) <= 800 or any(type(t) is not int for t in times):
            return {}
        dated = [datetime.fromtimestamp(t, KST).date().isoformat() for t in times]
        if sorted(set(dated)) != dated or dated[-1] > days[-1]:
            return {}
        return {day: _price(px) for day, px in zip(dated, close) if day in days}
    except (KeyError, TypeError, ValueError, IndexError, OverflowError, OSError):
        return {}


def fetch_yahoo(target, days):
    market, code = target
    ticker = code + ('.KS' if market == 'KOSPI' else '.KQ')
    start = datetime.combine(date.fromisoformat(days[0]), time.min, KST)
    end = datetime.combine(date.fromisoformat(days[-1]) + timedelta(days=1), time.min, KST)
    try:
        result = requests.get(YAHOO + ticker, params={
            'period1': int(start.timestamp()), 'period2': int(end.timestamp()), 'interval': '1d',
        }, headers={'User-Agent': 'Mozilla/5.0'}, timeout=(5, 12), allow_redirects=False)
        if result.status_code != 200 or len(result.content) > 160_000:
            return {}
        return yahoo_closes(result.json(), ticker, days)
    except (requests.RequestException, ValueError):
        return {}


def select(market, priority, report, current_listing):
    """Sample mismatch rows in 90-first order; balance both KRX markets."""
    day = market['date']
    if (priority.get('asof') != day or report.get('asof') != day
        or priority.get('operational_source_digest') != report.get('source_digest')
        or priority.get('listed_total') != len(report.get('rows', {}))):
        raise CloseSourceError('priority_or_score_source_date_mismatch')
    stocks = {s['code']: s for s in market['stocks']}
    if len(stocks) != len(market['stocks']):
        raise CloseSourceError('duplicate_saved_stock_codes')
    focus = priority['coverage_90_first']['family_conditional_priority_codes']['price_chart_windows']
    if type(focus) is not list or len(set(focus)) != len(focus):
        raise CloseSourceError('invalid_chart_priority')
    closes = cache_close_index(current_listing)
    groups = {market: [] for market in MARKETS}
    for code in focus:
        s = stocks.get(code)
        row = report['rows'].get(code)
        if (not s or not row or s.get('price_date') != day or s['market'] not in MARKETS
            or (s['market'], code) not in closes or not 0 <= row['available'] < 90):
            continue
        key = (s['market'], code)
        saved = _price(s.get('price'))
        if saved is not None and closes[key] is not None and saved != closes[key]:
            groups[s['market']].append(key)
    targets = [key for exchange in MARKETS for key in groups[exchange][:MAX_SAMPLE//2]]
    if not targets:
        raise CloseSourceError('empty_historical_source_sample')
    return targets, {name: len(entries) for name, entries in groups.items()}


def official_closes(client, day, targets):
    result = {}
    for market in MARKETS:
        rows, raw = client.daily_market(market, day)
        _snapshot_market(rows, raw, market=market, asof=day)
        for row in rows:
            key = (market, row['ISU_CD'])
            if key in targets and classify_row(row)[0] == 'valid_numeric_observation':
                result[key] = _price(row.get('TDD_CLSPRC'))
    return result


def compare(days, targets, *, client, loader, fetch=fetch_yahoo, official=official_closes):
    validate_dates(days)
    if not 1 <= len(targets) <= MAX_SAMPLE or len(set(targets)) != len(targets):
        raise CloseSourceError('invalid_historical_source_sample')
    keys = set(targets)
    observed = {day: official(client, day, keys) for day in days}
    cache = {day: cache_close_index(loader(day)[0]) for day in days}
    with ThreadPoolExecutor(max_workers=3) as workers:
        candidates = dict(zip(targets, workers.map(lambda key: fetch(key, days), targets)))
    result = {}
    full_span = {'public_krx_cache': 0, 'yahoo_daily': 0}
    for day in days:
        counts = {name: Counter() for name in full_span}
        for key in targets:
            ref = observed[day].get(key)
            for source, px in (('public_krx_cache', cache[day].get(key)),
                               ('yahoo_daily', candidates[key].get(day))):
                reason = ('official_unavailable' if ref is None else
                          'source_unavailable' if px is None else
                          'exact_close' if px == ref else 'different_close')
                counts[source][reason] += 1
        result[day] = {source: {k: v[k] for k in OUTCOMES} for source, v in counts.items()}
    for key in targets:
        if all(observed[day].get(key) is not None for day in days):
            full_span['public_krx_cache'] += all(
                cache[day].get(key) == observed[day][key] for day in days)
            full_span['yahoo_daily'] += all(
                candidates[key].get(day) == observed[day][key] for day in days)
    return {'sessions': result, 'exact_match_all_sampled_sessions': full_span,
            'krx_api_requests': 2 * len(days), 'public_cache_requests': len(days),
            'alternative_source_requests': len(targets)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    p.add_argument('--docs', type=Path, default=Path('docs'))
    a = p.parse_args(argv)
    if a.mode == 'dry-run':
        print(json.dumps({'mode': a.mode, 'asof': DATES[-1], 'sample_sessions': DATES,
                          'maximum_alternative_requests': MAX_SAMPLE, 'provider_requests': 0}))
        return
    key = os.environ.get('KRX_API_KEY')
    if not key or not key.strip():
        raise CloseSourceError('official_krx_key_unavailable')
    def read(name):
        return json.loads((a.docs / name).read_text(encoding='utf-8'))
    market = read('data.json')
    if market.get('date') != DATES[-1]:
        raise CloseSourceError('historical_sample_market_date_mismatch')
    listing, _ = load_dated_listing(DATES[-1])
    targets, sizes = select(market, read('v3-operational-evidence-priority.json'),
                            read('v3-operational-research-scores.json'), listing)
    data = compare(DATES, targets, client=KRXClient(key),
                   loader=lambda day: (listing, None) if day == DATES[-1] else load_dated_listing(day))
    output = {'schema': SCHEMA, 'asof': DATES[-1], 'price_priority_mismatch_issuers': sizes,
              'sample_size': len(targets), **data,
              'regular_session_history_verified': False, 'corporate_actions_verified': False,
              'consolidated_volume_verified': False, 'raw_prices_or_codes_saved': False,
              'available_weight_changed': False}
    encoded = json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False)
    (a.docs / OUT).write_text(encoded + '\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error) if type(error) is CloseSourceError else safe_failure_reason(error)
        print(json.dumps({'status': 'blocked', 'reason': reason, 'available_weight_changed': False}))
        raise SystemExit(1)
