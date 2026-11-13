"""Restore missing 121-session momentum facts from official KRX closing prices."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from official_probe.clients import KRXClient, ProbeError
from official_probe.validation import _numeric
from v3_krx_history_collection import MARKETS, _snapshot_market
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_saved_close_crosscheck import safe_failure_reason
from v3_krx_session_candidates import candidate_dates
from v3_krx_money_forward import RetryingMoneyClient
from v3_path_provisional_factors import CLOSE_PATH_SCHEMA, MOMENTUM_MAX
from v3_unscored_path_observations import close_path_facts

OUTPUT = 'v3-krx-close-momentum-recovery.json'
STOCK_FIELD = 'v3_krx_close_momentum_path'
SESSIONS = 121
MAX_TARGETS = 100


def select_targets(market, report, ledger):
    day = market['date']
    stocks = market['stocks']
    if (report['asof'] != day or ledger['market_asof'] != day
            or len({s['code'] for s in stocks}) != len(stocks)):
        raise ValueError('unaligned_close_momentum_sources')
    targets = {}
    for s in stocks:
        code = s['code']
        row = report['rows'][code]
        factor = row['parts']['momentum'].get('existing_momentum')
        baseline = ledger['rows'][code]['factors'].get('momentum')
        if (s.get('price_date') == day and s['market'] in MARKETS
            and 90-MOMENTUM_MAX <= row['available'] < 100
            and factor is not None and factor[1:] == [8, day]
            and baseline is not None and baseline['available'] == 8
            and baseline['asof'] == day
            and (s.get(STOCK_FIELD) or {}).get('asof') != day):
            targets[(s['market'], code)] = s
    if len(targets) > MAX_TARGETS:
        raise ValueError('close_momentum_candidate_budget_exceeded')
    return targets


def closes_on(client, market, day, codes):
    rows, raw = client.daily_market(market, day)
    _snapshot_market(rows, raw, market=market, asof=day)
    found = {}
    for row in rows:
        code = row['ISU_CD']
        if code not in codes:
            continue
        try:
            close = _numeric(row.get('TDD_CLSPRC'), 'TDD_CLSPRC', positive=True)
            change = str(row.get('CMPPREVDD_PRC', '')).strip().replace(',', '')
            found[code] = (
                close,
                _numeric(row.get('LIST_SHRS'), 'LIST_SHRS', positive=True),
                close-int(change) if re.fullmatch(r'[+-]?[0-9]+', change) else None,
            )
        except ProbeError:
            continue
    return found


def make_receipt(observations, *, asof):
    if len(observations) != SESSIONS or any(row is None for row in observations):
        return 'incomplete_close_history', None
    for previous, current in zip(observations, observations[1:]):
        previous_close, previous_shares, _ = previous
        _, shares, reference_price = current
        if previous_shares != shares and reference_price != previous_close:
            return 'listed_share_change_reference_discontinuous_or_missing', None
        if reference_price is not None and reference_price != previous_close:
            return 'official_reference_discontinuity', None
    close = [row[0] for row in observations]
    facts = close_path_facts(close)
    if facts is None:
        return 'invalid_or_discontinuous_close_history', None
    return 'recovered', {'schema': CLOSE_PATH_SCHEMA, 'asof': asof,
                         'path_window_trading_sessions': SESSIONS, **facts}


def run(market, report, ledger, key):
    day = market['date']
    targets = select_targets(market, report, ledger)
    start = (date.fromisoformat(day) - timedelta(days=210)).isoformat()
    dates = candidate_dates(calendar_weekday_candidates(start, day), source='xkrx')['dates'][-SESSIONS:]
    if len(dates) != SESSIONS or dates[-1] != day:
        raise ValueError('invalid_close_momentum_session_window')

    def collect(exchange):
        wanted = {code: s for (m, code), s in targets.items() if m == exchange}
        counts = Counter()
        if not wanted:
            return counts, {}, 0
        client = RetryingMoneyClient(KRXClient(key))
        try:
            current = closes_on(client, exchange, day, wanted)
            eligible = {c for c, s in wanted.items()
                        if c in current and current[c][0] == s['price']}
            counts['current_close_or_basis_unmatched'] = len(wanted) - len(eligible)
            if not eligible:
                return counts, {}, client.requests
            history = [closes_on(client, exchange, d, eligible) for d in dates[:-1]]
            history.append(current)
            receipts = {}
            for code in sorted(eligible):
                outcome, receipt = make_receipt([d.get(code) for d in history], asof=day)
                counts[outcome] += 1
                if receipt is not None:
                    receipts[(exchange, code)] = receipt
            return counts, receipts, client.requests
        finally:
            client.client.session.close()

    counts = Counter()
    calls = 0
    with ThreadPoolExecutor(max_workers=len(MARKETS)) as pool:
        for reasons, receipts, requests in pool.map(collect, MARKETS):
            counts.update(reasons)
            calls += requests
            for identity, receipt in receipts.items():
                targets[identity][STOCK_FIELD] = receipt
    return {'asof': day, 'first_source_session': dates[0], 'session_count': SESSIONS,
            'selected': len(targets), 'counts': dict(counts), 'official_requests': calls,
            'raw_source_exported': False, 'volume_evidence_used': False,
            'corporate_actions_adjusted_verified': False}


def main():
    root = Path('docs')
    raw = (root/'data.json').read_bytes()
    market = json.loads(raw)
    report = json.loads((root/'v3-operational-research-scores.json').read_bytes())
    ledger_name = 'v3-issuer-last-observed-factors.json'
    ledger_raw = (root/ledger_name).read_bytes()
    for name, payload in (('data.json', raw), (ledger_name, ledger_raw)):
        if sha256(payload).hexdigest() != report['source_hashes'].get(name):
            raise ValueError('unaligned_close_momentum_sources')
    ledger = json.loads(ledger_raw)
    result = run(market, report, ledger, os.environ.get('KRX_API_KEY', ''))
    result['input_market_sha256'] = sha256(raw).hexdigest()
    if result['counts'].get('recovered'):
        (root/'data.json').write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False),
                                     encoding='utf-8')
    text = json.dumps(result, sort_keys=True, allow_nan=False)
    (root/OUTPUT).write_text(text+'\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'close_momentum_recovery_blocked',
                          'reason':safe_failure_reason(error)}))
        raise SystemExit(1)
