"""Dry-run by default. Opt-in live KRX/DART probe after approval + authorization."""
from __future__ import annotations
import argparse
from datetime import date, timedelta, datetime, timezone
import json
import os
import re
from pathlib import Path

from .clients import KRXClient, DARTClient, ProbeError, canonical_date, code6
from .validation import inspect_krx, filter_visible_filings, public_safety_summary
from .financial import sample_annual_financial
from .history import sample_history
from .revisions import audit_annual_revisions
from .xbrl_compare import compare_annual_xbrl

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


def _batch_cases(text: str, asof: str) -> dict[str, tuple[str, str]]:
    """Strictly bounded, manually reviewed stock:receipt:scope pilot input.

    No receipt discovery, guessing or automatic API access occurs here.
    """
    if not isinstance(text, str):
        raise ValueError('XBRL pilot cases require explicit text')
    pieces = text.split(',')
    if not 2 <= len(pieces) <= 5:
        raise ValueError('XBRL pilot requires 2-5 explicitly listed cases')
    result = {}
    first_allowed = f"{int(asof[:4]):04d}0101"
    cutoff = canonical_date(asof)
    for piece in pieces:
        matched = re.fullmatch(r'(\d{6}):(\d{14}):(OFS|CFS)', piece.strip())
        if not matched:
            raise ValueError('Each XBRL pilot case must be CODE:RECEIPT:OFS|CFS')
        stock, receipt, scope = matched.groups()
        if stock in result:
            raise ValueError('Duplicate XBRL pilot stock code')
        try:
            date.fromisoformat(receipt[:4] + '-' + receipt[4:6] + '-' + receipt[6:8])
        except ValueError:
            raise ValueError('XBRL pilot receipt contains invalid date') from None
        if not first_allowed <= receipt[:8] < cutoff:
            raise ValueError('XBRL pilot receipt must be dated within the completed asof window')
        result[stock] = (receipt, scope)
    return result


def run(argv=None, *, krx_session=None, dart_session=None):
    p = argparse.ArgumentParser(description='Bounded official data connection smoke test; NO automatic publishing')
    p.add_argument('--asof', required=True, help='YYYY-MM-DD, use a completed trading date')
    p.add_argument('--symbols', help='comma separated codes drawn from docs/data.json (max 10)')
    p.add_argument('--source', default=str(DEFAULT_SOURCE))
    p.add_argument('--provider', choices=('krx','dart','both'), default='both')
    p.add_argument('--live', action='store_true', help='requires approved provider access and credentials')
    p.add_argument('--dart-limit', type=int, default=2, help='number of DART sample firms (1 or 2)')
    p.add_argument('--finance-sample', action='store_true', help='request prior-year CFS sample; not usable as PIT financials')
    p.add_argument('--history-sample', action='store_true', help='one-company three-year annual + Q1/H1 raw DART field study')
    p.add_argument('--revision-audit', action='store_true', help='one-company annual correction timeline + two original document hashes')
    p.add_argument('--revision-expected-receipt', help='14-digit report receipt previously observed from OpenDART financial sample')
    p.add_argument('--xbrl-compare', action='store_true', help='one-company original annual XBRL vs current DART finance API comparison')
    p.add_argument('--xbrl-receipt', help='14-digit verified annual filing receipt for XBRL comparison')
    p.add_argument('--xbrl-batch-cases', help='2-5 explicit CODE:RECEIPT:OFS|CFS records; DART live research only')
    p.add_argument('--xbrl-fs-div', choices=('OFS', 'CFS'), default='OFS', help='accounting basis must be explicitly selected in manual workflow')
    p.add_argument('--output', help='local file location; do NOT place under docs/')
    args = p.parse_args(argv)
    if args.finance_sample and (not args.live or args.provider != 'dart'):
        p.error('Finance sample is permitted only for explicit DART live runs')
    if args.history_sample and (not args.live or args.provider != 'dart' or args.finance_sample or args.revision_audit):
        p.error('History sample requires DART-only live run and excludes finance or revision audit')
    if args.revision_audit:
        if (not args.live or args.provider != 'dart' or args.finance_sample or args.history_sample
                or not args.revision_expected_receipt):
            p.error('Revision audit requires DART-only live, no other samples, and prior financial receipt')
        if not re.fullmatch(r'\d{14}', args.revision_expected_receipt):
            p.error('Prior financial receipt must be exactly 14 digits')
    elif args.revision_expected_receipt:
        p.error('A revision receipt cannot be submitted unless revision audit is selected')
    if args.xbrl_batch_cases:
        if (not args.live or args.provider != 'dart' or args.finance_sample
                or args.history_sample or args.revision_audit
                or args.xbrl_compare or args.xbrl_receipt
                or args.revision_expected_receipt):
            p.error('XBRL batch pilot requires DART live only, with all other samples disabled')
    if args.xbrl_compare:
        if (not args.live or args.provider != 'dart' or args.finance_sample
                or args.history_sample or args.revision_audit
                or not args.xbrl_receipt or not re.fullmatch(r'\d{14}', args.xbrl_receipt)):
            p.error('XBRL comparison requires DART live, prior receipt and no other sample')
    elif args.xbrl_receipt:
        p.error('An XBRL receipt requires --xbrl-compare')
    canonical_date(args.asof)
    try:
        pilot_cases = _batch_cases(args.xbrl_batch_cases, args.asof) if args.xbrl_batch_cases else {}
    except ValueError as exc:
        p.error(str(exc))
    codes, markets, doc = _symbols(Path(args.source), args.symbols)
    if pilot_cases and (not args.symbols or list(pilot_cases) != codes):
        p.error('XBRL pilot cases must match all explicitly selected symbols in the same order')
    if args.history_sample and (len(codes) != 1 or args.dart_limit not in (1, 2)):
        p.error('History sample requires exactly one explicit stock symbol')
    if args.revision_audit and (len(codes) != 1 or not args.symbols):
        p.error('Revision audit requires exactly one explicitly selected stock')
    if args.xbrl_compare and (len(codes) != 1 or not args.symbols):
        p.error('XBRL comparison requires exactly one explicitly selected stock')
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
                # Pilot mode is separately limited to 2-5 cases by preflight.
                for code in (codes if pilot_cases else codes[:args.dart_limit]):
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
                            annual_year = int(args.asof[:4]) - 1
                            financial = sample_annual_financial(
                                client, corp, annual_year, args.asof, fs_div='CFS')
                            if financial['status'] == 'annual_statement_not_available':
                                financial = sample_annual_financial(
                                    client, corp, annual_year, args.asof, fs_div='OFS')
                                financial['used_OFS_fallback'] = True
                            item['financial_sample'] = financial
                        except (ProbeError, ValueError):
                            item['financial_sample'] = {
                                'status': 'failed', 'metric_values': None,
                                'asof_join_validated': False,
                                'error': 'DART annual filing/financial response could not be verified'}
                    if args.history_sample:
                        try:
                            item['history_sample'] = sample_history(
                                client, corp, args.asof, years=3)
                        except (ProbeError, ValueError):
                            item['history_sample'] = {
                                'status': 'failed', 'investment_data_ready': False,
                                'revision_history_validated': False,
                                'error': 'DART history transport or report evidence not verified'}
                    if args.revision_audit:
                        try:
                            item['revision_audit'] = audit_annual_revisions(
                                client, corp, int(args.asof[:4]) - 1, args.asof,
                                args.revision_expected_receipt,
                                download_documents=True)
                        except (ProbeError, ValueError):
                            item['revision_audit'] = {
                                'status': 'failed', 'financial_numeric_changes_verified': False,
                                'revision_history_validated': False, 'investment_data_ready': False,
                                'error': 'DART filing chronology or original document evidence unavailable'}
                    if args.xbrl_compare or pilot_cases:
                        try:
                            receipt, scope = (
                                pilot_cases[code] if pilot_cases else
                                (args.xbrl_receipt, args.xbrl_fs_div))
                            item['xbrl_comparison'] = compare_annual_xbrl(
                                client, corp, int(args.asof[:4]) - 1, args.asof,
                                receipt, fs_div=scope)
                        except (ProbeError, ValueError):
                            item['xbrl_comparison'] = {
                                'status': 'failed_or_unavailable',
                                'metrics': None,
                                'historical_numeric_snapshot_verified': False,
                                'xbrl_accounting_scope_verified': False,
                                'confirmed_scoring_allowed': False,
                                'investment_data_ready': False,
                                'error': 'XBRL archive or account comparison unavailable or unsafe'}
                    sample.append(item)
                out['dart']={'status':'observed_unverified','corp_mapped_requested':sum(c in mapping for c in codes),
                             'companies_sampled':sample,'financial_asof_join_verified':False}
                if pilot_cases:
                    out['dart']['xbrl_batch_pilot'] = {
                        'mode': 'manual_explicit_receipts_research_only',
                        'requested_cases': len(pilot_cases),
                        'completed_case_reports': sum('xbrl_comparison' in s for s in sample),
                        'accounting_scope_verified': False,
                        'point_in_time_validated': False,
                        'investment_data_ready': False,
                    }
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
