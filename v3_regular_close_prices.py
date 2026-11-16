"""Publish public-cache closes only after matching retained official KRX evidence."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from v3_dated_market_roster import DatedRosterError, load_dated_listing
from v3_fdr_cache_krx_close_corroboration import cache_close_index
from v3_forward_daily_prices import OUT as DAILY_STATUS, SCHEMA as DAILY_SCHEMA, read_daily
from v3_forward_share_actions import normalized_name
from v3_score_observation_capture import positive

SCHEMA = 'v3-current-corroborated-regular-closes-1'
OUT = Path('docs/v3-regular-close-prices.json')
MARKET = Path('docs/data.json')


class RegularCloseError(ValueError):
    """Fixed diagnostics without source data."""


def build(market, source_digest, frame, cache_digest, daily):
    day = market['date']
    if (daily['date'] != day or not re.fullmatch(r'[a-f0-9]{64}', source_digest)
            or not re.fullmatch(r'[a-f0-9]{64}', cache_digest)
            or len({row['code'] for row in market['stocks']}) != len(market['stocks'])):
        raise RegularCloseError('invalid_regular_close_sources')
    closes = cache_close_index(frame)
    names = {(('KOSDAQ' if row['Market'] == 'KOSDAQ GLOBAL' else row['Market']), row['Code']):
             normalized_name(row['Name']) for _, row in frame.iterrows()}
    rows, counts = {}, Counter()
    for stock in market['stocks']:
        code, exchange = stock['code'], stock['market']
        key = exchange, code
        row = daily['rows'].get(code)
        state = 'matched'
        if (stock.get('price_date') != day or not positive(stock.get('price'))):
            state = 'analysis_price_unavailable'
        elif key not in closes or not positive(closes[key]):
            state = 'public_close_unavailable'
        elif row is None:
            state = 'official_row_absent'
        elif (row[0] != exchange or not normalized_name(stock['name'])
                or normalized_name(row[1]) != normalized_name(stock['name'])
                or names.get(key) != normalized_name(stock['name'])):
            state = 'identity_unresolved'
        elif row[2] != 'traded':
            state = 'official_traded_price_unavailable'
        elif closes[key] != row[6]:
            state = 'public_official_close_mismatch'
        counts[state] += 1
        if state == 'matched':
            rows[code] = {'name': stock['name'], 'market': exchange,
                          'analysis_close': stock['price'], 'close': closes[key]}
    return {
        'schema': SCHEMA, 'asof': day,
        'source': 'DATED_PUBLIC_FDR_CLOSE_CORROBORATED_WITH_KRX',
        'source_market_sha256': source_digest, 'public_cache_sha256': cache_digest,
        'official_source_sha256': daily['source_sha256'],
        'requested_count': len(market['stocks']), 'matched_count': len(rows),
        'changed_display_price_count': sum(row['analysis_close'] != row['close'] for row in rows.values()),
        'states': dict(counts), 'historical_ohlcv_regular_session_verified': False,
        'score_recalculated': False, 'rows': rows,
    }


def publish_regular_closes(get_storage):
    raw = MARKET.read_bytes()
    market = json.loads(raw)
    digest = sha256(raw).hexdigest()
    status_file = DAILY_STATUS / f"{market['date']}.json"
    status = json.loads(status_file.read_bytes()) if status_file.exists() else {}
    if status.get('status') != 'archived_privately':
        return
    if status.get('schema') != DAILY_SCHEMA or status.get('date') != market['date']:
        raise RegularCloseError('invalid_regular_close_daily_status')
    prior = json.loads(OUT.read_bytes()) if OUT.exists() else {}
    if (prior.get('schema') == SCHEMA and prior.get('asof') == market['date']
            and prior.get('source_market_sha256') == digest
            and prior.get('official_source_sha256') == status['source_sha256']):
        return
    daily = read_daily(market['date'], get_storage())
    if daily is None or daily['source_sha256'] != status['source_sha256']:
        raise RegularCloseError('regular_close_daily_source_mismatch')
    try:
        frame, cache_digest = load_dated_listing(market['date'])
    except DatedRosterError as error:
        if str(error) != 'dated_roster_unavailable':
            raise RegularCloseError('invalid_public_regular_close_source') from None
        print('waiting_for_dated_regular_close_source')
        return
    report = build(market, digest, frame, cache_digest, daily)
    # The crosscheck uses the official panel in memory. Published quotes
    # below are selected from the separately downloaded public CSV.
    OUT.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':'),
                              allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in (
        'asof', 'requested_count', 'matched_count', 'changed_display_price_count', 'states')}))
