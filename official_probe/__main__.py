"""Dry-run by default. Opt-in live KRX/DART probe after approval + authorization."""
from __future__ import annotations
import argparse
from datetime import date, timedelta, datetime, timezone
import json
import os
from pathlib import Path

from .clients import KRXClient, DARTClient, ProbeError, canonical_date, code6
from .validation import inspect_krx, filter_visible_filings, public_safety_summary
from .financial import sample_annual_financial

DEFAULT_SOURCE = Path('docs/data.json')


def _symbols(path: Path, selection: str | None):
    if selection:
        values = [code6(c.strip()) for c in selection.split(',') if c.strip()]
    else:
        doc = json.loads(path.read_text(encoding='utf-8'))
        values = [code6(s['code']) for s in doc['stocks'][:10]]
    if not values or len(values) > 10 or len(set(values)) != len(values):
        raise ValueError('Probe requires 1–10 unique six-digit stock codes')
    doc = json.loads(path.read_text(encoding='utf-8'))
    market = {code6(s['code']): s.get('market') for s in doc['stocks']}
    known = [c for c in values if market.get(c) not in ('KOSPI', 'KOSDAQ')]
    if known:
        raise ValueError('Requested symbol absent from current market snapshot; no guessed exchange')
    return values, market, doc


def run(argv=None, *, krx_session=None, dart_session=None):
    p = argparse.ArgumentParser(description='Bounded official data connection smoke test; NO automatic publishing')
    p.add_argument('--asof', required=True, help='YYYY-MM-DD, use a completed trading date')
    p.add_argument('--symbols', help='comma separated codes drawn from docs/data.json (max 10)')
    p.add_argument('--source', default=str(DEFAULT_SOURCE))
    p.add_argument('--provider', choices=('krx','dart','both'), default='both')
    p.add_argument('--live', action='store_true', help='requires approved provider access and credentials')
    p.add_argument('--dart-limit', type=int, default=2, help='number of DART sample firms (1 or 2)')
    p.add_argument('--finance-sample', action='store_true', help='request prior-year CFS sample; not usable as PIT financials')
    p.add_argument('--output', help='local file location; do NOT place under docs/')
    args = p.parse_args(argv)
    if args.finance_sample and (not args.live or args.provider != 'dart'):
        p.error('Finance sample is permitted only for explicit DART live runs')
    canonical_date(args.asof)
    codes, markets, doc = _symbols(Path(args.source), args.symbols)
    if args.dart_limit not in (1, 2):
        p.error('--dart-limit must be 1 or 2')
    if args.asof > str(doc['date']):
        p.error('Probe date must not exceed existing docs/data.json snapshot date')
    out = {
        'schema_version': 'official-probe-0.1', 'asof': args.asof,
        'mode': 'live' if args.live else 'dry_run', 'source_snapshot_date': doc['date'],
        'requested_symbols': codes,
        'investment_data_ready': False, 'confirmed_scoring_allowed': False,
        'krx_regular_only_volume_verified': False, 'investor_flows_verified': False,
        'krx': {'status': 'not_requested'}, 'dart': {'status': 'not_requested'},
        'warnings': ['Probe results are not a regular-session, full-history or backtest validation.',
                     'KRX ACC_TRDVOL must not be treated as regular-only volume without proof.',
                     'KRX public daily-stock API does not by itself establish institutional/foreign net buying.',
                     'Do not publish probe snapshots to GitHub Pages or treat missing fields as zero.'],
    }
    if args.provider in ('krx','both'):
        key = os.getenv('KRX_API_KEY')
        if not args.live:
            out['krx'] = {'status': 'dry_run', 'markets': sorted({markets[c] for c in codes}),
                          'live_requests_if_authorized': len({markets[c] for c in codes})}
        elif not key:
            out['krx'] = {'status': 'key_required', 'secret_name': 'KRX_API_KEY'}
        else:
            try:
                client = KRXClient(key, session=krx_session)
                groups = {market: {c for c in codes if markets[c] == market}
                          for market in ('KOSPI','KOSDAQ')}
                observed = []
                for market, group in groups.items():
                    if not group:
                        continue
                    rows, raw = client.daily_market(market, args.asof)
                    info = inspect_krx(rows, market, args.asof, group, raw)
                    observed.append({**{k:v for k,v in info.items() if k!='symbol_data'},
                                     'symbol_data': {c:public_safety_summary(r) for c,r in info['symbol_data'].items()}})
                out['krx'] = {'status': ('observed_unverified' if any(m['found_symbols'] for m in observed)
                                     else 'requested_symbols_not_found'),
                              'markets': observed, 'approved_endpoint_scope_needs_confirmation': True}
            except (ProbeError, ValueError):
                out['krx'] = {'status': 'failed', 'error': 'KRX authorization, transport or response schema not confirmed'}
    if args.provider in ('dart','both'):
        key = os.getenv('DART_API_KEY')
        if not args.live:
            out['dart'] = {'status': 'dry_run', 'companies_planned': min(len(codes),args.dart_limit)}
        elif not key:
            out['dart'] = {'status': 'key_required', 'secret_name': 'DART_API_KEY'}
        else:
            try:
                client = DARTClient(key, session=dart_session)
                mapping = client.corp_mapping()
                sample = []
                start = (date.fromisoformat(args.asof)-timedelta(days=90)).isoformat()
                for code in codes[:args.dart_limit]:
                    corp = mapping.get(code)
                    if not corp:
                        sample.append({'code': code, 'status': 'corp_code_unmapped'})
                        continue
                    filings = client.recent_filings(corp,start,args.asof)
                    valid = filter_visible_filings(filings,args.asof)
                    item = {'code':code,'corp_code':corp,'status':'observed_not_pit_validated',
                            'filings_returned_first_page':len(filings),
                            'filings_before_asof':len(valid),
                            'same_day_or_invalid_filings_excluded':len(filings)-len(valid),
                            'financial_metrics_verified':False}
                    if args.finance_sample:
                        try:
                            item['financial_sample'] = sample_annual_financial(
                                client, corp, int(args.asof[:4])-1, args.asof)
                        except (ProbeError, ValueError):
                            item['financial_sample'] = {
                                'status': 'failed', 'metric_values': None,
                                'asof_join_validated': False,
                                'error': 'DART annual filing/financial response could not be verified'}
                    sample.append(item)
                out['dart']={'status':'observed_unverified','corp_mapped_requested':sum(c in mapping for c in codes),
                             'companies_sampled':sample,'financial_asof_join_verified':False}
            except (ProbeError, ValueError):
                out['dart']={'status':'failed','error':'OpenDART authorization, transport or response schema not confirmed'}
    # Do not make reports date-keyed to current time: source asof is the only analytical date.
    out['received_at_utc'] = datetime.now(timezone.utc).isoformat()
    output = Path(args.output) if args.output else Path('evidence/official_probe')/('probe_'+args.asof.replace('-','')+'.json')
    if output.parts and (output.parts[0] == 'docs' or '..' in output.parts):
        p.error('Output must remain outside docs/ and may not use parent traversal')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'output':str(output),'mode':out['mode'],'krx':out['krx']['status'],
                      'dart':out['dart']['status'],'investment_data_ready':False},ensure_ascii=False))
    return out


if __name__ == '__main__':
    run()
