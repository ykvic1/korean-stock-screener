"""Rebuild missing public momentum observations from stored official closes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from v3_dart_bulk_history import _stable_digest
from v3_path_provisional_factors import recovered_close_momentum

OUTPUT = 'v3-public-saved-close-momentum.json'
SCHEMA = 'v3-public-saved-close-momentum-1'


def binding(market, catalog):
    return {
        'schema': SCHEMA,
        'asof': catalog['date'],
        'market_digest': _stable_digest(market),
        'catalog_digest': _stable_digest(catalog),
    }


def observations(report, market, catalog):
    if not isinstance(report, dict) or market['date'] != catalog['date']:
        return {}
    if any(report.get(key) != value for key, value in binding(market, catalog).items()):
        return {}
    rows = report.get('rows')
    if not isinstance(rows, dict):
        return {}
    return {code: points for code, row in rows.items()
            if (points := recovered_close_momentum(row, catalog['date'])) is not None
            and row.get('close_anchor') == 'saved_close_krx_match'}


def build(market, catalog, indicator_report, read_session):
    from v3_forward_share_actions import normalized_name
    from v3_krx_close_momentum_recovery import SESSIONS, load_bonus_notices, load_face_notices, make_receipt
    from v3_regular_close_contract import admitted_rows
    from v3_regular_close_indicators import history_dates

    day = catalog['date']
    official = admitted_rows(indicator_report, catalog)
    if not official:
        return None, Counter({'official_indicator_report_not_admitted': 1})
    members = {row['code']: row for row in catalog['rows']}
    candidates = {
        stock['code']: stock for stock in market['stocks']
        if stock.get('price_date') == day
        and stock.get('v3_unscored_path_observations') is None
        and stock['code'] in members
        and official.get(stock['code'], {}).get('continuous_sessions', 0) >= SESSIONS
    }
    dates = history_dates(day)[-SESSIONS:]
    if len(dates) != SESSIONS or dates[-1] != day:
        raise ValueError('invalid_saved_close_momentum_calendar')

    def matches(record, stock, member):
        return (record is not None and record[0] == stock['market'] == member['market']
                and normalized_name(record[1]) == normalized_name(stock['name'])
                == normalized_name(member['name']))

    result = {**binding(market, catalog), 'rows': {}}
    if not candidates:
        return result, Counter()
    current = read_session(day)
    if current is None:
        return None, Counter({'missing_current_archive_day': 1})
    current_rows = current['rows']
    accepted = {
        code: stock for code, stock in candidates.items()
        if matches(current_rows.get(code), stock, members[code])
        and current_rows[code][5] is True and current_rows[code][2] == stock.get('price')
    }

    def load_rows(session):
        saved = current if session == day else read_session(session)
        if saved is None:
            return None
        rows = saved['rows']
        return {code: rows.get(code) for code in accepted}

    reasons = Counter()
    if not accepted:
        return result, reasons
    with ThreadPoolExecutor(max_workers=6) as pool:
        history = list(pool.map(load_rows, dates))
    if any(rows is None for rows in history):
        return None, Counter({'missing_archived_session': 1})

    face_notices, _ = load_face_notices()
    bonus_notices = load_bonus_notices()
    for code, stock in accepted.items():
        member = members[code]
        records = [rows.get(code) for rows in history]
        if not all(matches(row, stock, member) for row in records):
            reasons['missing_or_unmatched_identity'] += 1
            continue
        if any(row[5] is False and row[2] != row[4] for row in records):
            reasons['changed_close_without_reported_trade'] += 1
            continue
        reason, receipt = make_receipt(
            [row[2:5] for row in records], asof=day, sessions=dates,
            face_notices={session: notice for session, notice in face_notices.get(code, {}).items()
                          if notice.get('market', 'KOSDAQ') == member['market']},
            bonus_notices={session: notice for session, notice in bonus_notices.get(code, {}).items()
                           if notice['market'] == member['market']},
        )
        reasons[reason] += 1
        if receipt is not None:
            result['rows'][code] = {**receipt, 'close_anchor': 'saved_close_krx_match'}
    return result, reasons


def main():
    import os

    from v3_regular_close_indicators import PRIVATE_REPO, PrivateGitStorage, archive_path, read_day

    root = Path('docs')
    market = json.loads((root / 'data.json').read_bytes())
    catalog = json.loads((root / 'v22-market-catalog.json').read_bytes())
    indicator_file = root / 'v3-regular-close-indicators.json'
    if not indicator_file.exists() or market['date'] != catalog['date']:
        return
    indicators = json.loads(indicator_file.read_bytes())
    if indicators.get('asof') != catalog['date']:
        return
    destination = root / OUTPUT
    if destination.exists():
        prior = json.loads(destination.read_bytes())
        if all(prior.get(key) == value for key, value in binding(market, catalog).items()):
            return
    if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
        raise ValueError('private_regular_close_destination_unavailable')
    storage = PrivateGitStorage(PRIVATE_REPO, os.environ['V3_PRIVATE_ARCHIVE_TOKEN'])
    storage.verify_private()

    def read_session(day):
        blob, _ = storage.get_content(archive_path(day))
        return read_day(blob, day)

    report, reasons = build(market, catalog, indicators, read_session)
    if report is not None:
        destination.write_text(json.dumps(report, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'public_momentum_recovery': dict(reasons), 'provider_requests': 0}))


if __name__ == '__main__':
    main()
