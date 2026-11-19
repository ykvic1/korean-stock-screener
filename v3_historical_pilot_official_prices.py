"""Compare the pinned historical pilot with official KRX rows, in memory only."""
import argparse
from collections import Counter
import csv
from datetime import datetime, time
from hashlib import sha256
import json
import math
import os
from pathlib import Path

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import daily_row, number
from v3_forward_entry import read_official_openings
from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import END, ENTRY, ROOT, SIGNAL, MARKETS, day, ledger_rows, read_panels, require, traded
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS
from v3_krx_history_collection import HistoryCollectionError, validate_dates

SCHEMA = 'v3-historical-pilot-official-prices-v1'
# Keep each authenticated batch within the existing five-date client limit.
BATCHES = {
    'anchor': (SIGNAL, ENTRY, day('2026-01-13'), day('2026-05-13'), END),
    'remaining': tuple(map(day, ('2025-12-15', '2026-02-13', '2026-09-14', '2026-10-19'))),
    # The five previously unchecked dates most frequently used as observed
    # adverse extremes in the 85 historical delisting horizon rows.
    'downside_priority': tuple(map(day, ('2025-11-19', '2025-12-08', '2025-12-09',
                                        '2026-01-08', '2026-01-12'))),
    # Next five dates by adverse-extreme frequency, breaking ties by date.
    'downside_followup': tuple(map(day, ('2025-11-21', '2025-12-01', '2025-12-18',
                                        '2026-01-05', '2026-01-07'))),
    # Remaining adverse-extreme dates in the pinned delisting pilot.
    'downside_tail_1': tuple(map(day, ('2025-11-18', '2025-11-24', '2025-12-12',
                                      '2025-12-26', '2026-01-09'))),
    'downside_tail_2': tuple(map(day, ('2026-02-02', '2026-02-06', '2026-03-04',
                                      '2026-03-24', '2026-04-01'))),
    'downside_tail_3': tuple(map(day, ('2026-04-06', '2026-04-28', '2026-05-12',
                                      '2026-06-24', '2026-06-29'))),
    'downside_tail_4': tuple(map(day, ('2026-07-03', '2026-08-20', '2026-09-11',
                                      '2026-09-18', '2026-10-30'))),
    'downside_tail_5': tuple(map(day, ('2026-11-06', '2026-11-12'))),
}
STRICT_PRICE_BATCHES = {name for name in BATCHES if name.startswith('downside_')}
PRICE_FIELDS = ('open', 'high', 'low', 'close')


def compare_market(public, official, codes):
    keys = set(codes)
    pairs = keys & public.keys() & official.keys()
    counts = Counter({
        'scope': len(keys),
        'absent_from_public': len(keys - public.keys()),
        'absent_from_official': len(keys - official.keys()),
        'identity_mismatch': 0,
        'listed_share_mismatch': 0,
        'listed_shares_unavailable': 0,
        'positive_trade_on_both': 0,
        'positive_trade_on_public_only': 0,
        'positive_trade_on_official_only': 0,
        'no_positive_trade_on_either': 0,
        'confirmed_zero_volume_on_both': 0,
        'exact_ohlc': 0,
    })
    difference = dict.fromkeys(PRICE_FIELDS, 0)
    for code in pairs:
        p, o = public[code], official[code]
        if p[0] != o[0] or normalized_name(p[1]) != normalized_name(o[1]):
            counts['identity_mismatch'] += 1
            continue
        if not p[2] or not o[7]:
            counts['listed_shares_unavailable'] += 1
        elif p[2] != o[7]:
            counts['listed_share_mismatch'] += 1
        p_trade, o_trade = traded(p), o[2] == 'traded'
        if p_trade and o_trade:
            counts['positive_trade_on_both'] += 1
            different = tuple(lhs != rhs for lhs, rhs in zip(p[3:7], o[3:7], strict=True))
            counts['exact_ohlc'] += not any(different)
            for field, delta in zip(PRICE_FIELDS, different, strict=True):
                difference[field] += delta
        elif p_trade:
            counts['positive_trade_on_public_only'] += 1
        elif o_trade:
            counts['positive_trade_on_official_only'] += 1
        else:
            counts['no_positive_trade_on_either'] += 1
            counts['confirmed_zero_volume_on_both'] += (
                public_quantity(p[7]) == 0 and o[2] == 'no_reported_volume')
    return {**counts, 'different_price_by_field': difference}



TURNOVER_FIELDS = {'volume': 'ACC_TRDVOL', 'amount': 'ACC_TRDVAL'}



def public_quantity(value):
    """Accept exact whole quantities, never rounded or unsafe float values."""
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float:
        return (int(value) if math.isfinite(value) and value.is_integer()
                and 0 <= value <= 2**53 - 1 else None)
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def read_turnover_panels(paths, sessions):
    """Read only pinned turnover fields; retain issuer rows in memory."""
    for path, digest in zip(paths, SOURCE_DIGESTS, strict=True):
        require(sha256(path.read_bytes()).hexdigest() == digest,
                'historical_public_source_digest_mismatch')
    import pyarrow.parquet as pq

    sessions = set(sessions)
    panels = {session: {} for session in sessions}

    def add(session, row):
        if session not in panels or row['Market'] not in MARKETS:
            return
        code = row['Code']
        require(code and code not in panels[session], 'duplicate_turnover_market_security')
        values = []
        for key in ('Volume', 'Amount'):
            value = row[key]
            values.append(public_quantity(value))
        panels[session][code] = (MARKETS[row['Market']], row['Name'], *values)

    columns = ['Date', 'Code', 'Market', 'Name', 'Volume', 'Amount']
    for path in paths[:2]:
        table = pq.read_table(path, columns=columns, filters=[
            ('Date', 'in', [datetime.combine(session, time.min) for session in sorted(sessions)]),
            ('Market', 'in', list(MARKETS)),
        ])
        for batch in table.to_batches():
            for row in batch.to_pylist():
                add(row['Date'].date(), row)
    require(END not in sessions or not panels[END], 'duplicate_turnover_market_endpoint')
    with paths[2].open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            add(END, row)
    return panels


def compare_turnover(public, official):
    """Count exact quantities and KRW amounts without exporting values or codes."""
    counts = Counter(dict(scope=len(public.keys() | official.keys()),
                          absent_from_public=len(official.keys() - public.keys()),
                          absent_from_official=len(public.keys() - official.keys()),
                          identity_mismatch=0, exact_volume_and_amount=0))
    fields = {key: Counter(dict(compared=0, matched=0, mismatched=0,
                               public_unavailable=0, official_unavailable=0,
                               both_zero=0)) for key in TURNOVER_FIELDS}
    for code in public.keys() & official.keys():
        market, row = official[code]
        p = public[code]
        if market != p[0] or normalized_name(row.get('ISU_NM', '')) != normalized_name(p[1]):
            counts['identity_mismatch'] += 1
            continue
        exact = True
        for index, (key, source_field) in enumerate(TURNOVER_FIELDS.items(), 2):
            lhs = public_quantity(p[index])
            rhs = number(row, source_field)
            f = fields[key]
            f['public_unavailable'] += lhs is None
            f['official_unavailable'] += rhs is None
            if lhs is None or rhs is None:
                exact = False
                continue
            f['compared'] += 1
            f['matched'] += lhs == rhs
            f['mismatched'] += lhs != rhs
            f['both_zero'] += lhs == rhs == 0
            exact = exact and lhs == rhs
        counts['exact_volume_and_amount'] += exact
    return {**counts, 'fields': fields,
            'numeric_equivalence_is_not_session_certification': True}


def exact_market_prices(comparison):
    """Require identity, share counts, trade status and all traded OHLC values."""
    return (0 < comparison['exact_ohlc']
            and comparison['exact_ohlc'] + comparison['confirmed_zero_volume_on_both'] == comparison['scope']
            and comparison['listed_share_mismatch'] == comparison['listed_shares_unavailable'] == 0)


def audit(panels, delisted, client, batch, *, turnover_panels=None):
    sessions = BATCHES[batch]
    validate_dates([d.isoformat() for d in sessions])
    anchor = panels[SIGNAL]
    require(delisted <= anchor.keys(), 'delisted_security_outside_pilot_anchor')
    results = {}
    errors = {}
    for session in sessions:
        public = panels[session]
        try:
            raw, hashes = read_official_openings(client, session.isoformat())
        except (KRXProbeError, HistoryCollectionError) as error:
            reason = error.reason if isinstance(error, KRXProbeError) else str(error)
            errors[session.isoformat()] = {
                'reason': reason, 'market': error.market,
                'market_responses': getattr(error, 'market_responses', None),
            }
            if reason in {'http_unauthorized', 'http_forbidden', 'http_rate_limited'}:
                break
            continue
        official = {code: daily_row(market, row) for code, (market, row) in raw.items()}
        anchor_listed = anchor.keys() & (public.keys() | official.keys())
        results[session.isoformat()] = {
            'official_source_sha256': hashes,
            'turnover': (compare_turnover(turnover_panels[session], raw)
                         if turnover_panels is not None else None),
            'market_wide': compare_market(public, official, public.keys() | official.keys()),
            'present_pilot_anchor_issuers': compare_market(public, official, anchor_listed),
            'pilot_anchor_absent_from_both': len(anchor.keys() - anchor_listed),
            'later_delisted_pilot_issuers': compare_market(public, official, delisted),
        }
    turnover = [item['turnover'] for item in results.values()]
    turnover_complete = (
        turnover_panels is not None and len(turnover) == len(sessions)
        and all(item is not None and item['scope'] > 0
                and all(field['compared'] == item['scope'] for field in item['fields'].values())
                for item in turnover))
    turnover_matched = (turnover_complete and all(
        all(field['matched'] == item['scope'] for field in item['fields'].values())
        for item in turnover))
    return {
        'market_wide_ohlc_and_membership_matched': len(results) == len(sessions) and all(
            exact_market_prices(item['market_wide']) for item in results.values()),
        'turnover_numeric_comparison_complete': turnover_complete,
        'turnover_all_values_matched': turnover_matched,
        'schema': SCHEMA,
        'batch': batch,
        'signal_session': SIGNAL.isoformat(),
        'entry_session': ENTRY.isoformat(),
        'public_panel_source_sha256': list(SOURCE_DIGESTS),
        'sessions': results,
        'failed_sessions': errors,
        'requested_sessions': [d.isoformat() for d in sessions],
        'maximum_official_api_requests': 2 * len(sessions),
        'adjusted_price_history_verified': False,
        'volume_session_equivalence_verified': False,
        'performance_validated': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs=3, type=Path)
    parser.add_argument('--batch', choices=BATCHES, required=True)
    args = parser.parse_args()
    key = os.environ.get('KRX_API_KEY', '')
    if not key.strip():
        raise SystemExit('official_krx_key_unavailable')
    panels = read_panels(args.sources)
    delisted = {row['code'] for row in ledger_rows(ROOT, 'delistings')
                if row['anchor_membership'] == 'present'}
    turnover = read_turnover_panels(args.sources, BATCHES[args.batch])
    result = audit(panels, delisted, KRXClient(key), args.batch, turnover_panels=turnover)
    print(json.dumps(result, sort_keys=True))
    if (result['failed_sessions'] or not result['turnover_all_values_matched']
            or (args.batch in STRICT_PRICE_BATCHES and not result['market_wide_ohlc_and_membership_matched'])):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
