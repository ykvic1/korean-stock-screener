"""Corroborate independently published closes against retained KRX evidence."""
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

SCHEMA = 'v3-current-corroborated-regular-closes-2'
OUT = Path('docs/v3-regular-close-prices.json')
MARKET = Path('docs/data.json')


class RegularCloseError(ValueError):
    """Fixed diagnostics without source data."""


def build(market, source_digest, frame, cache_digest, daily):
    day = market['date']
    if (daily['date'] != day or not re.fullmatch(r'[a-f0-9]{64}', source_digest)
            or (frame is None) != (cache_digest is None)
            or (cache_digest is not None and not re.fullmatch(r'[a-f0-9]{64}', cache_digest))
            or len({row['code'] for row in market['stocks']}) != len(market['stocks'])):
        raise RegularCloseError('invalid_regular_close_sources')
    closes = cache_close_index(frame) if frame is not None else {}
    names = {(('KOSDAQ' if row['Market'] == 'KOSDAQ GLOBAL' else row['Market']), row['Code']):
             normalized_name(row['Name']) for _, row in frame.iterrows()} if frame is not None else {}
    rows, counts = {}, Counter()
    sources = dict.fromkeys(('FDR_DATED_LISTING', 'PUBLISHED_SCREENER_PRICE'), 0)
    for stock in market['stocks']:
        code, exchange = stock['code'], stock['market']
        key = exchange, code
        row = daily['rows'].get(code)
        state = 'matched'
        public_source = None
        public_close = closes.get(key)
        name = normalized_name(stock['name'])
        if (stock.get('price_date') != day or not positive(stock.get('price'))):
            state = 'analysis_price_unavailable'
        elif row is None:
            state = 'official_row_absent'
        elif row[0] != exchange or not name or normalized_name(row[1]) != name:
            state = 'identity_unresolved'
        elif row[2] != 'traded' or not positive(row[6]):
            state = 'official_traded_price_unavailable'
        elif positive(public_close) and names.get(key) == name and public_close == row[6]:
            public_source = 'FDR_DATED_LISTING'
        elif stock['price'] == row[6]:
            public_source, public_close = 'PUBLISHED_SCREENER_PRICE', stock['price']
        elif not positive(public_close):
            state = 'public_close_unavailable'
        elif names.get(key) != name:
            state = 'identity_unresolved'
        else:
            state = 'public_official_close_mismatch'
        counts[state] += 1
        if state == 'matched':
            sources[public_source] += 1
            rows[code] = {'name': stock['name'], 'market': exchange,
                          'analysis_close': stock['price'], 'close': public_close,
                          'public_source': public_source}
    return {
        'schema': SCHEMA, 'asof': day,
        'source': 'PUBLIC_CLOSES_CORROBORATED_WITH_KRX',
        'source_market_sha256': source_digest, 'public_cache_sha256': cache_digest,
        'official_source_sha256': daily['source_sha256'],
        'requested_count': len(market['stocks']), 'matched_count': len(rows),
        'matches_by_public_source': sources,
        'changed_display_price_count': sum(row['analysis_close'] != row['close'] for row in rows.values()),
        'states': dict(counts), 'historical_ohlcv_regular_session_verified': False,
        'score_recalculated': False, 'rows': rows,
    }


def publish_regular_closes(get_storage, *, retry_public_cache=False):
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
    same_inputs = (prior.get('schema') == SCHEMA and prior.get('asof') == market['date']
            and prior.get('source_market_sha256') == digest
            and prior.get('official_source_sha256') == status['source_sha256'])
    if same_inputs and (not retry_public_cache or prior['matched_count'] == prior['requested_count']):
        return
    try:
        frame, cache_digest = load_dated_listing(market['date'])
    except DatedRosterError as error:
        if str(error) != 'dated_roster_unavailable':
            raise RegularCloseError('invalid_public_regular_close_source') from None
        print('waiting_for_dated_regular_close_source')
        frame, cache_digest = None, None
    if same_inputs and (cache_digest is None or cache_digest == prior['public_cache_sha256']):
        return
    daily = read_daily(market['date'], get_storage())
    if daily is None or daily['source_sha256'] != status['source_sha256']:
        raise RegularCloseError('regular_close_daily_source_mismatch')
    report = build(market, digest, frame, cache_digest, daily)
    if same_inputs and not prior['rows'].keys() <= report['rows'].keys():
        print('preserving_regular_closes_after_public_cache_revision')
        return
    # The crosscheck uses the official panel in memory. Published quotes
    # below are selected from the public CSV or already published screener.
    OUT.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':'),
                              allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in (
        'asof', 'requested_count', 'matched_count', 'matches_by_public_source',
        'changed_display_price_count', 'states')}))
