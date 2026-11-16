"""Cross-check the listed-share and quote anchors of reviewed corporate actions."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import daily_row
from v3_forward_entry import read_official_openings
from v3_historical_market_coverage import ROOT, day, ledger_rows, read_panels
from v3_historical_pilot_official_prices import compare_market
from v3_krx_history_collection import HistoryCollectionError, validate_dates

# Each batch uses at most four previously unchecked sessions (two market calls each).
BATCHES = {
    'split_and_reduction': (('share-adjustments', '008290'), ('share-adjustments', '028080')),
    'january_exchanges': (('successor-listings', '450140'), ('successor-listings', '042670')),
    'february_and_april_exchanges': (('successor-listings', '019440'), ('successor-listings', '138490')),
    'july_and_august_exchanges': (('successor-listings', '057050'), ('successor-listings', '082640')),
    'treasury_delivery_and_consolidation': (
        ('successor-listings', '031440'), ('successor-listings', '299900'),
    ),
}


def event_anchors(ledger, notice):
    if ledger == 'share-adjustments':
        code = notice['code']
        before = notice['prior_trade_listed_shares']
        after = notice['shares_after_action']
    else:
        code = notice['successor_code']
        before = notice['shares_before']
        after = notice['shares_after']
    return code, (
        (day(notice['prior_trade_on']), int(before)),
        (day(notice['listing_on']), int(after)),
    )


def stage_outcome(public, official, code, shares):
    comparison = compare_market(public, official, (code,))
    for key in ('absent_from_public', 'absent_from_official', 'identity_mismatch',
                'listed_shares_unavailable', 'listed_share_mismatch'):
        if comparison[key]:
            return key
    if public[code][2] != shares:
        return 'ledger_share_count_mismatch'
    if not comparison['positive_trade_on_both']:
        return 'trade_or_prices_unavailable'
    if not comparison['exact_ohlc']:
        return 'ohlc_mismatch'
    return 'matched'


def audit(batch, panels, client):
    ledgers = {key: {row['code']: row for row in ledger_rows(ROOT, key)}
               for key in ('share-adjustments', 'successor-listings')}
    events = [event_anchors(key, ledgers[key][code]) for key, code in BATCHES[batch]]
    sessions = sorted({session for _, anchors in events for session, _ in anchors})
    validate_dates([session.isoformat() for session in sessions])
    results = {}
    errors = {}
    evidence = {}
    for session in sessions:
        date = session.isoformat()
        try:
            rows, hashes = read_official_openings(client, date)
        except (KRXProbeError, HistoryCollectionError) as error:
            reason = error.reason if isinstance(error, KRXProbeError) else str(error)
            errors[date] = {'reason': reason, 'market': error.market,
                            'market_responses': getattr(error, 'market_responses', None)}
            if reason in {'http_unauthorized', 'http_forbidden', 'http_rate_limited'}:
                break
            continue
        results[session] = {code: daily_row(market, row) for code, (market, row) in rows.items()}
        evidence[date] = hashes

    outcomes = {'prior_trade': Counter(), 'listing_day': Counter()}
    both = 0
    for code, anchors in events:
        states = []
        for stage, (session, shares) in zip(outcomes, anchors, strict=True):
            state = (stage_outcome(panels[session], results[session], code, shares)
                     if session in results else 'official_session_unavailable')
            states.append(state)
            outcomes[stage][state] += 1
        both += all(state == 'matched' for state in states)

    return {
        'schema': 'v3-historical-corporate-action-price-anchors-v1',
        'batch': batch,
        'event_count': len(events),
        'both_official_anchors_matched': both,
        'anchor_outcomes': outcomes,
        'requested_sessions': [session.isoformat() for session in sessions],
        'official_source_sha256': evidence,
        'failed_sessions': errors,
        'adjusted_price_history_verified': False,
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
    result = audit(args.batch, read_panels(args.sources), KRXClient(key))
    print(json.dumps(result, sort_keys=True))
    if result['failed_sessions'] or result['both_official_anchors_matched'] != result['event_count']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
