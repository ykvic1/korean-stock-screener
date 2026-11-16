"""Privately retain one KRX daily price panel per session for all forward cohorts."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
from io import BytesIO
import json
from pathlib import Path
import re

from official_probe.clients import KRXProbeError
from v3_forward_horizon_readiness import HORIZONS, _anniversary
from v3_krx_derived_5day import _private_new_blob_readback
from v3_krx_history_collection import MARKETS
from v3_krx_private_daily import IngestError, OHLC, _whole
from v3_krx_regular_close import ANNOUNCED_CLOSES, KST, regular_close_utc
from v3_krx_session_candidates import CONFIRMED_KRX_2026_CLOSURES

OUT = Path('ops/forward-daily-price-status')
SCHEMA = 'v3-forward-krx-daily-price-evidence-1'
ROW_FIELDS = ('market', 'name', 'status', 'open', 'high', 'low', 'close', 'listed_shares')
ROW_STATES = ('traded', 'no_reported_volume', 'price_unavailable')
MAX_SESSIONS_PER_RUN = 5
MAX_PRIVATE_BYTES = 2_000_000
SHA = re.compile(r'[a-f0-9]{64}\Z')
CODE = re.compile(r'[A-Z0-9]{6}\Z')


class DailyEvidenceError(ValueError):
    """Fixed errors allowed in public logs."""


def number(row, field):
    try:
        return _whole(row.get(field), field)
    except IngestError:
        return None


def daily_row(market, row):
    name = row.get('ISU_NM')
    name = name.strip() if isinstance(name, str) else ''
    shares = number(row, 'LIST_SHRS') or None
    volume = number(row, 'ACC_TRDVOL')
    op, high, low, close = (number(row, field) for field in OHLC.values())
    valid = (all(value is not None for value in (op, high, low, close))
             and 0 < low <= min(op, close) <= max(op, close) <= high)
    if volume == 0:
        return [market, name, 'no_reported_volume', None, None, None, None, shares]
    if volume is None or not valid:
        return [market, name, 'price_unavailable', None, None, None, None, shares]
    return [market, name, 'traded', op, high, low, close, shares]


def completed_sessions(start, end, now):
    """Use exchange calendar candidates and the same known overrides as entry."""
    import exchange_calendars as xc

    end = min(end, now.astimezone(KST).date())
    if start > end:
        return
    calendar = xc.get_calendar('XKRX')
    for session in calendar.sessions_in_range(start, end):
        day = session.date()
        if day.isoformat() in CONFIRMED_KRX_2026_CLOSURES:
            continue
        close = (regular_close_utc(day) if day in ANNOUNCED_CLOSES
                 else calendar.session_close(session).to_pydatetime())
        if close <= now:
            yield day.isoformat()


def daily_summary(evidence, day):
    hashes = evidence['source_sha256']
    received = datetime.fromisoformat(evidence['recorded_at_utc'])
    rows = evidence['rows']
    if (evidence['schema'] != SCHEMA or evidence['date'] != day
            or received.utcoffset() != timedelta(0)
            or received < regular_close_utc(date.fromisoformat(day))
            or tuple(evidence['row_fields']) != ROW_FIELDS
            or set(hashes) != set(MARKETS)
            or any(not isinstance(digest, str) or not SHA.fullmatch(digest)
                   for digest in hashes.values())
            or not isinstance(rows, dict) or not 0 < len(rows) <= 40_000):
        raise DailyEvidenceError('invalid_private_daily_price_archive')
    counts = Counter()
    missing_shares = 0
    for code, record in rows.items():
        market, name, state, op, high, low, close, shares = record
        if (not CODE.fullmatch(code) or market not in MARKETS or not isinstance(name, str)
                or state not in ROW_STATES
                or (shares is not None and (type(shares) is not int or shares <= 0))):
            raise DailyEvidenceError('invalid_private_daily_price_row')
        prices = (op, high, low, close)
        if state == 'traded':
            if (any(type(value) is not int for value in prices)
                    or not 0 < low <= min(op, close) <= max(op, close) <= high):
                raise DailyEvidenceError('invalid_private_daily_traded_prices')
        elif any(value is not None for value in prices):
            raise DailyEvidenceError('unavailable_private_daily_price_has_value')
        counts[market, state] += 1
        missing_shares += shares is None
    if any(not sum(counts[m, s] for s in ROW_STATES) for m in MARKETS):
        raise DailyEvidenceError('incomplete_private_daily_price_pair')
    return {
        'schema': SCHEMA, 'date': day, 'status': 'archived_privately',
        'source_sha256': hashes, 'recorded_at_utc': evidence['recorded_at_utc'],
        'daily_row_count': len(rows),
        'market_price_counts': {market: {state: counts[market, state] for state in ROW_STATES}
                                for market in MARKETS},
        'unavailable_listed_shares_count': missing_shares,
        'price_source': 'KRX_OFFICIAL_STOCK_DAILY_OHLC',
        'adjusted_history_certified': False,
        'corporate_actions_certified': False,
        'performance_validated': False,
    }


def read_daily(day, storage):
    path = f'derived/forward-daily-prices/{day}.json.gz'
    blob, _ = storage.get_content(path)
    if blob is None:
        return None
    try:
        with gzip.GzipFile(fileobj=BytesIO(blob)) as stream:
            raw = stream.read(MAX_PRIVATE_BYTES + 1)
        if len(raw) > MAX_PRIVATE_BYTES:
            raise DailyEvidenceError('oversized_private_daily_price_archive')
        evidence = json.loads(raw)
        daily_summary(evidence, day)
        return evidence
    except DailyEvidenceError:
        raise
    except (TypeError, KeyError, OSError, EOFError, ValueError):
        raise DailyEvidenceError('unreadable_private_daily_price_archive') from None


def save_daily(day, storage, official_pair, *, clock=None):
    path = f'derived/forward-daily-prices/{day}.json.gz'
    old = read_daily(day, storage)
    if old is not None:
        return daily_summary(old, day)
    observations, hashes = official_pair(day)
    clock = clock or (lambda: datetime.now(timezone.utc))
    evidence = {
        'schema': SCHEMA, 'date': day, 'source_sha256': hashes, 'row_fields': ROW_FIELDS,
        'recorded_at_utc': clock().isoformat(),
        'rows': {code: daily_row(market, row)
                 for code, (market, row) in observations.items()},
    }
    summary = daily_summary(evidence, day)
    raw = json.dumps(evidence, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_PRIVATE_BYTES:
        raise DailyEvidenceError('oversized_daily_evidence')
    blob = gzip.compress(raw, mtime=0)
    storage.put_new(path, blob, 'Archive KRX daily OHLC evidence for ' + day)
    _private_new_blob_readback(storage, path, blob)
    return summary


def collect_daily(entry_statuses, now, get_storage, official_pair, *, clock=None):
    if not entry_statuses:
        return
    entries = [date.fromisoformat(item['entry_session_candidate']) for item in entry_statuses]
    signal_days = [date.fromisoformat(item.get('score_asof', item['entry_session_candidate']))
                   for item in entry_statuses]
    # Keep collecting through the target-session selection margin used by the
    # forward horizon audit, including when the anniversary falls on a holiday.
    last_target = _anniversary(max(entries), max(HORIZONS)) + timedelta(days=10)
    pending = []
    for day in completed_sessions(min(signal_days), last_target, now):
        target = OUT / f'{day}.json'
        if target.exists():
            previous = json.loads(target.read_bytes())
            if previous.get('schema') != SCHEMA or previous.get('date') != day:
                raise DailyEvidenceError('invalid_public_daily_status')
            if previous['status'] == 'archived_privately':
                continue
            attempted = previous['last_source_attempt_at_utc']
        else:
            attempted = ''
        pending.append((attempted, day, target))
    if not pending:
        return
    OUT.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    for _, day, target in sorted(pending)[:MAX_SESSIONS_PER_RUN]:
        try:
            result = save_daily(day, storage, official_pair, clock=clock)
        except KRXProbeError as error:
            if error.reason not in ('empty_body', 'empty_market_rows'):
                raise DailyEvidenceError('krx_' + error.reason) from None
            result = {'schema': SCHEMA, 'date': day,
                      'status': 'waiting_for_official_paired_response',
                      'last_source_attempt_at_utc': now.isoformat(),
                      'unavailable_market': error.market, 'source_reason': error.reason}
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
