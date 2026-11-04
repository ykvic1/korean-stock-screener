"""Source-gated personal valuation20: 4/4/4/4/2/2, with dated retention.

This is a transparent preference model, not calibrated fair value or returns.
Reported positive-path EPS CAGR saturates at 20%; stability is 4/(1+CV).
Zero/loss transitions are observed but receive no invented positive CAGR.
"""
from __future__ import annotations
from datetime import date
import math

from v3_dart_reported_eps import YEAR, EPS_IDS, plan, metrics, EPSError
from v3_dart_bulk_history import _valid_receipt

MODEL = 'v3-personal-valuation-reported-eps-20-v1'
WEIGHTS = {'pbr': 4, 'earnings_yield_pct': 4, 'eps_growth': 4,
           'eps_stability': 4, 'fcf_yield': 2, 'sales_to_market_cap': 2}
BASE = ('pbr', 'earnings_yield_pct', 'fcf_yield', 'sales_to_market_cap')


def r6(value):
    return math.floor(value * 1e6 + .5) / 1e6


def evaluate(eps, baseline):
    observed = metrics(eps)
    if not observed['complete_three_years'] or set(baseline) != set(BASE) or any(
        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 5 for v in baseline.values()):
        raise ValueError('valuation_eps_or_baseline_not_observed')
    positive = all(v > 0 for v in eps)
    growth = 100 * (math.sqrt(eps[2] / eps[0]) - 1) if positive else None
    mean = (eps[0] + eps[1] + eps[2]) / 3 if positive else None
    cv = math.sqrt(((eps[0]-mean)**2 + (eps[1]-mean)**2 + (eps[2]-mean)**2)/3)/mean if positive else None
    parts = {'pbr': r6(baseline['pbr'] * .8),
             'earnings_yield_pct': r6(baseline['earnings_yield_pct'] * .8),
             'eps_growth': r6(4 * max(0, min(1, growth / 20))) if positive else 0,
             'eps_stability': r6(4 / (1 + cv)) if positive else 0,
             'fcf_yield': r6(baseline['fcf_yield'] * .4),
             'sales_to_market_cap': r6(baseline['sales_to_market_cap'] * .4)}
    total = 0.0
    for key in WEIGHTS:
        total += parts[key]
    return parts, r6(total)


def valid(record, *, code, name, market, day):
    try:
        if (type(record) is not dict or record.get('model') != MODEL
            or record.get('code') != code or record.get('name') != name or record.get('market') != market
            or record.get('fiscal_year') != YEAR or record.get('fs_div') not in ('CFS', 'OFS')
            or record.get('source_account_id') not in EPS_IDS
            or record.get('investment_score_certified') is not False
            or type(record.get('retained')) is not bool
            or not '2000-01-01' <= record['asof'] <= day
            or date.fromisoformat(record['asof']).isoformat() != record['asof']
            or date.fromisoformat(record['observed_on']).isoformat() != record['observed_on']
            or record['observed_on'] > record['asof']):
            return False
        dates = record['baseline_dates']
        if (type(dates) is not dict or set(dates) != set(BASE)
            or any(type(d) is not str or not '2000-01-01' <= d <= record['asof']
                   or date.fromisoformat(d).isoformat() != d for d in dates.values())
            or record['source_oldest_asof'] != min(dates.values())):
            return False
        receipt = record['annual_receipt_id']
        if type(receipt) is not str or len(receipt) != 14 or not receipt.isdigit():
            return False
        if not _valid_receipt(receipt, YEAR, date.fromisoformat(record['observed_on'])):
            return False
        parts, points = evaluate(record['eps'], record['baseline'])
        return (type(record['points']) in (int, float) and math.isfinite(record['points'])
                and type(record['parts']) is dict
                and all(type(v) in (int, float) and math.isfinite(v) for v in record['parts'].values())
                and record['parts'] == parts and record['points'] == points
                and record['weights'] == WEIGHTS)
    except (ValueError, KeyError, TypeError, OverflowError):
        return False


def build(catalog, book, eps_source, ledger, prior=None):
    day = catalog['date']
    result = {}
    members = {r['code']: r for r in catalog['rows']}
    if isinstance(prior, dict):
        for code, record in prior.items():
            m = members.get(code)
            if m and valid(record, code=code, name=m['name'], market=m['market'], day=day):
                result[code] = {**record, 'retained': True}
    if book is None or eps_source is None:
        return result, 'waiting_reported_eps_source'
    try:
        _, _, _, observed, _ = plan(catalog, book, eps_source)
    except (EPSError, ValueError, TypeError, KeyError):
        return result, 'reported_eps_source_not_aligned'
    for code, source in observed.items():
        if not source['metrics']['complete_three_years']:
            continue
        saved = ledger['rows'].get(code) or {}
        aggregate = saved.get('factors', {}).get('valuation') or {}
        components = saved.get('components', {}).get('valuation') or {}
        if (aggregate.get('available') != 20 or not isinstance(aggregate.get('asof'), str)
            or not '2000-01-01' <= aggregate['asof'] <= day):
            continue
        baseline, dates = {}, {}
        for key in BASE:
            entry = components.get(key) or {}
            expected_source = 'dart_fcf_subfactor' if key == 'fcf_yield' else 'dart_fiscal_subfactor'
            if (not isinstance(entry.get('asof'), str)
                or not '2000-01-01' <= entry['asof'] <= day or entry.get('available') != 5
                or entry.get('source') != expected_source):
                break
            try:
                if date.fromisoformat(entry['asof']).isoformat() != entry['asof']:
                    break
            except ValueError:
                break
            baseline[key] = entry.get('points')
            dates[key] = entry['asof']
        if len(baseline) != 4:
            continue
        try:
            parts, points = evaluate(source['reported_basic_eps_2023_2025'], baseline)
            # A prior day's 20-point aggregate must not override newer
            # individual source components. Only compare totals when every
            # component is actually from the aggregate's observation day.
            if (all(d == aggregate['asof'] for d in dates.values())
                and abs(sum(baseline.values()) - aggregate['points']) > .00001):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        record = {k: source[k] for k in ('code', 'name', 'market', 'fiscal_year', 'fs_div',
                                         'annual_receipt_id', 'source_account_id', 'observed_on')}
        record.update(model=MODEL, asof=day, eps=source['reported_basic_eps_2023_2025'],
                      baseline=baseline, baseline_dates=dates, source_oldest_asof=min(dates.values()),
                      parts=parts, points=points, weights=dict(WEIGHTS),
                      investment_score_certified=False, retained=False)
        if valid(record, code=code, name=source['name'], market=source['market'], day=day):
            result[code] = record
    return result, 'receipt_matched_reported_eps'
