"""Compare a frozen published price panel with its signal-day official KRX close."""
from collections import Counter
from functools import lru_cache
from hashlib import sha256
import gzip
import json
from pathlib import Path
import re
import subprocess

from v3_forward_daily_prices import OUT as DAILY_STATUS, SCHEMA as DAILY_SCHEMA, read_daily
from v3_forward_share_actions import normalized_name
from v3_krx_derived_5day import _private_new_blob_readback
from v3_score_observation_capture import ARCHIVE, MARKET_PATH, positive

OUT = Path('ops/forward-signal-close-status')
SCHEMA = 'v3-forward-signal-close-comparison-1'
MAX_COHORTS_PER_RUN = 5
STATES = ('matched', 'close_mismatch', 'published_row_absent', 'published_price_unavailable',
          'published_price_stale', 'official_row_absent', 'identity_unresolved',
          'no_reported_volume', 'official_price_unavailable')


class SignalCloseError(ValueError):
    """Fixed diagnostics suitable for public logs."""


def frozen_market(observation):
    """Recover exact source bytes, including after the current market file advances."""
    expected = observation['source_market_sha256']
    current = Path(MARKET_PATH)
    if current.exists():
        raw = current.read_bytes()
        if sha256(raw).hexdigest() == expected:
            return raw
    commit = observation.get('source_commit')
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise SignalCloseError('invalid_frozen_market_commit')
    command = ['git', 'show', f'{commit}:{MARKET_PATH}']
    try:
        raw = subprocess.check_output(command, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        try:
            subprocess.run(['git', 'fetch', '--no-tags', '--depth=1', 'origin', commit],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw = subprocess.check_output(command, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return None
    if sha256(raw).hexdigest() != expected:
        raise SignalCloseError('frozen_market_hash_mismatch')
    return raw


def compare_closes(observation, market_bytes, daily):
    if sha256(market_bytes).hexdigest() != observation['source_market_sha256']:
        raise SignalCloseError('frozen_market_hash_mismatch')
    market = json.loads(market_bytes)
    day = observation['score_asof']
    stocks = {row['code']: row for row in market['stocks']}
    if (market['date'] != day or daily['date'] != day
            or len(stocks) != len(market['stocks'])):
        raise SignalCloseError('invalid_signal_close_source')
    cases = {}
    counts = Counter()
    for code, member in observation['rows'].items():
        stock, row = stocks.get(code), daily['rows'].get(code)
        state = 'matched'
        if stock is None:
            state = 'published_row_absent'
        elif stock.get('price_date') != day:
            state = 'published_price_stale'
        elif not positive(stock.get('price')):
            state = 'published_price_unavailable'
        elif row is None:
            state = 'official_row_absent'
        elif (stock.get('market') != member[1] or row[0] != member[1]
                or not normalized_name(member[0])
                or normalized_name(stock.get('name')) != normalized_name(member[0])
                or normalized_name(row[1]) != normalized_name(member[0])):
            state = 'identity_unresolved'
        elif row[2] == 'no_reported_volume':
            state = 'no_reported_volume'
        elif row[2] != 'traded':
            state = 'official_price_unavailable'
        elif stock['price'] != row[6]:
            state = 'close_mismatch'
        counts[state] += 1
        cases[code] = {'state': state}
        if state == 'close_mismatch':
            cases[code].update(published_close=stock['price'], official_close=row[6])
    return {
        'schema': SCHEMA, 'score_asof': day, 'status': 'compared',
        'source_market_sha256': observation['source_market_sha256'],
        'official_source_sha256': daily['source_sha256'],
        'compared_count': len(cases), 'states': {state: counts[state] for state in STATES},
        'all_ohlcv_regular_session_certified': False, 'performance_validated': False,
    }, cases


def collect_signal_closes(entry_statuses, now, get_storage):
    @lru_cache(maxsize=MAX_COHORTS_PER_RUN)
    def daily_panel(day):
        return read_daily(day, get_storage())

    pending = []
    for entry in entry_statuses:
        day = entry['score_asof']
        target = OUT / f'{day}.json'
        previous = json.loads(target.read_bytes()) if target.exists() else {}
        status_path = DAILY_STATUS / target.name
        daily_status = json.loads(status_path.read_bytes()) if status_path.exists() else {}
        if (daily_status.get('status') == 'archived_privately'
                and (daily_status.get('schema') != DAILY_SCHEMA or daily_status.get('date') != day)):
            raise SignalCloseError('invalid_signal_daily_status')
        if (previous.get('schema') == SCHEMA and previous.get('status') == 'compared'
                and previous.get('score_asof') == day
                and previous.get('score_observation_sha256') == entry['score_observation_sha256']
                and previous.get('official_source_sha256') == daily_status.get('source_sha256')):
            continue
        pending.append((previous.get('last_attempt_at_utc', ''), day, target, entry, daily_status))
    for _, day, target, entry, status in sorted(pending, key=lambda item: item[:2])[:MAX_COHORTS_PER_RUN]:
        raw = (ARCHIVE / target.name).read_bytes()
        digest = sha256(raw).hexdigest()
        if digest != entry['score_observation_sha256']:
            raise SignalCloseError('signal_score_hash_changed')
        observation = json.loads(raw)
        if observation['score_asof'] != day:
            raise SignalCloseError('signal_score_filename_mismatch')
        result = {'schema': SCHEMA, 'score_asof': day,
                  'score_observation_sha256': digest, 'last_attempt_at_utc': now.isoformat(),
                  'status': 'waiting_for_signal_daily_archive'}
        if status.get('status') == 'archived_privately':
            daily = daily_panel(day)
            if daily is None or daily['source_sha256'] != status.get('source_sha256'):
                raise SignalCloseError('signal_daily_source_mismatch')
            market = frozen_market(observation)
            if market is None:
                result['status'] = 'waiting_for_frozen_market_source'
            else:
                summary, cases = compare_closes(observation, market, daily)
                summary['score_observation_sha256'] = digest
                payload = json.dumps({'summary': summary, 'cases': cases}, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()
                evidence_digest = sha256(payload).hexdigest()
                path = f'derived/forward-signal-closes/{day}/{evidence_digest}.json.gz'
                blob = gzip.compress(payload, mtime=0)
                storage = get_storage()
                old, _ = storage.get_content(path)
                if old is None:
                    storage.put_new(path, blob, 'Archive signal close comparison for ' + day)
                    _private_new_blob_readback(storage, path, blob)
                elif gzip.decompress(old) != payload:
                    raise SignalCloseError('signal_close_evidence_differs')
                result = {**summary, 'evidence_sha256': evidence_digest}
        OUT.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result))
