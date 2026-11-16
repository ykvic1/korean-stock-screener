"""Compare the pinned historical pilot with official KRX rows, in memory only."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import daily_row
from v3_forward_entry import read_official_openings
from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import END, ENTRY, ROOT, SIGNAL, day, ledger_rows, read_panels, require, traded
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS
from v3_krx_history_collection import HistoryCollectionError, validate_dates

SCHEMA = 'v3-historical-pilot-official-prices-v1'
# Keep each authenticated batch within the existing five-date client limit.
BATCHES = {
    'anchor': (SIGNAL, ENTRY, day('2026-01-13'), day('2026-05-13'), END),
    'remaining': tuple(map(day, ('2025-12-15', '2026-02-13', '2026-09-14', '2026-10-19'))),
}
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
    return {**counts, 'different_price_by_field': difference}


def audit(panels, delisted, client, batch):
    sessions = BATCHES[batch]
    validate_dates([d.isoformat() for d in sessions])
    anchor = panels[SIGNAL]
    require(delisted <= anchor.keys(), 'delisted_security_outside_pilot_anchor')
    results = {}
    for session in sessions:
        public = panels[session]
        raw, hashes = read_official_openings(client, session.isoformat())
        official = {code: daily_row(market, row) for code, (market, row) in raw.items()}
        anchor_listed = anchor.keys() & (public.keys() | official.keys())
        results[session.isoformat()] = {
            'official_source_sha256': hashes,
            'market_wide': compare_market(public, official, public.keys() | official.keys()),
            'present_pilot_anchor_issuers': compare_market(public, official, anchor_listed),
            'pilot_anchor_absent_from_both': len(anchor.keys() - anchor_listed),
            'later_delisted_pilot_issuers': compare_market(public, official, delisted),
        }
    return {
        'schema': SCHEMA,
        'batch': batch,
        'signal_session': SIGNAL.isoformat(),
        'entry_session': ENTRY.isoformat(),
        'public_panel_source_sha256': list(SOURCE_DIGESTS),
        'sessions': results,
        'official_api_requests': 2 * len(sessions),
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
    try:
        result = audit(panels, delisted, KRXClient(key), args.batch)
    except (KRXProbeError, HistoryCollectionError) as error:
        print(json.dumps({'status': 'official_pilot_spotcheck_failed',
                          'reason': error.reason if isinstance(error, KRXProbeError) else str(error),
                          'market': getattr(error, 'market', None),
                          'market_responses': getattr(error, 'market_responses', None)}))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
