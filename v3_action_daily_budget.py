"""Private, conservative daily reservations for the two action continuations.

Reserve before any provider call, and return unused capacity only after a
successful verified run. A crash keeps the full reservation charged. This
planning budget does not claim to measure other users of the shared DART key.
"""
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from v3_dart_historical_event_receipts import PRIVATE_REPO, _bytes as canonical
from v3_private_dart_structured_action_receipts import _write_checkpoint_with_retry, _same_bytes

SCHEMA = 'v3-private-action-daily-reservations-v1'
LIMITS = {'principal': (9216, 6), 'supplemental': (3840, 20)}
MAX_BYTES = 500_000


class DailyBudgetError(ValueError):
    """Static errors only."""


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def _day(day):
    try:
        if type(day) is not str or datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
            raise ValueError()
    except (ValueError, TypeError):
        raise DailyBudgetError('invalid_action_budget_day') from None
    return day


def _path(kind, day):
    if type(kind) is not str or kind not in LIMITS:
        raise DailyBudgetError('invalid_action_budget_kind')
    return 'derived/action-continuation-budgets/' + _day(day) + '/' + kind + '.json'


def _read(raw, kind, day):
    if raw is None:
        return {'schema': SCHEMA, 'kind': kind, 'day': day,
                'limit': LIMITS[kind][0], 'reservations': {}}
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
            raise ValueError()
        obj = json.loads(raw)
        if (set(obj) != {'schema', 'kind', 'day', 'limit', 'reservations'}
            or obj['schema'] != SCHEMA or obj['kind'] != kind or obj['day'] != day
            or type(obj['limit']) is not int or obj['limit'] != LIMITS[kind][0]
            or type(obj['reservations']) is not dict or len(obj['reservations']) > 2048):
            raise ValueError()
        for token, entry in obj['reservations'].items():
            if (re.fullmatch(r'[0-9a-f]{32}', token) is None or type(entry) is not dict
                or set(entry) != {'reserved', 'used'} or type(entry['reserved']) is not int
                or not LIMITS[kind][1] <= entry['reserved'] <= LIMITS[kind][0]
                or entry['used'] is not None and
                   (type(entry['used']) is not int or not 0 <= entry['used'] <= entry['reserved'])):
                raise ValueError()
        if _charged(obj) > obj['limit']:
            raise ValueError()
        return obj
    except Exception:
        raise DailyBudgetError('invalid_private_action_daily_budget') from None


def _charged(book):
    return sum(e['reserved'] if e['used'] is None else e['used']
               for e in book['reservations'].values())


def runtime_seconds(day, *, now=None, ceiling=3300):
    """Stop provider attempts before a reservation's UTC calendar day ends."""
    _day(day)
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise DailyBudgetError('action_budget_requires_utc_clock')
    if now.date().isoformat() != day:
        return 0
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, min(ceiling, int((midnight - now).total_seconds()) - 60))


def reserve(storage, kind, requested, *, day=None):
    day = utc_day() if day is None else _day(day)
    path = _path(kind, day)
    if (getattr(storage, 'repository', None) != PRIVATE_REPO
        or type(requested) is not int
        or not LIMITS[kind][1] <= requested <= LIMITS[kind][0]):
        raise DailyBudgetError('invalid_action_budget_reservation')
    storage.verify_private()
    raw, _ = storage.get_content(path)
    book = _read(raw, kind, day)
    available = book['limit'] - _charged(book)
    grant = min(requested, available)
    grant -= grant % LIMITS[kind][1]
    if grant < LIMITS[kind][1]:
        return None
    token = uuid4().hex
    if token in book['reservations']:
        raise DailyBudgetError('action_budget_reservation_collision')
    book['reservations'][token] = {'reserved': grant, 'used': None}
    encoded = canonical(book)
    _write_checkpoint_with_retry(storage, raw, encoded, checkpoint_path=path)
    _same_bytes(storage, path, encoded)
    return {'kind': kind, 'day': day, 'token': token, 'reserved': grant}


def finish(storage, ticket, used):
    if (type(ticket) is not dict or set(ticket) != {'kind', 'day', 'token', 'reserved'}
        or type(ticket['kind']) is not str or ticket['kind'] not in LIMITS or type(ticket['token']) is not str
        or re.fullmatch(r'[0-9a-f]{32}', ticket['token']) is None
        or type(ticket['reserved']) is not int or type(used) is not int
        or not 0 <= used <= ticket['reserved']
        or getattr(storage, 'repository', None) != PRIVATE_REPO):
        raise DailyBudgetError('invalid_action_budget_settlement')
    path = _path(ticket['kind'], ticket['day'])
    raw, _ = storage.get_content(path)
    book = _read(raw, ticket['kind'], ticket['day'])
    entry = book['reservations'].get(ticket['token'])
    if (entry is None or entry['reserved'] != ticket['reserved']
        or entry['used'] is not None and entry['used'] != used):
        raise DailyBudgetError('action_budget_settlement_changed')
    entry['used'] = used
    encoded = canonical(book)
    _write_checkpoint_with_retry(storage, raw, encoded, checkpoint_path=path)
    _same_bytes(storage, path, encoded)
    return book['limit'] - _charged(book)


def resumable(result, remaining, minimum):
    return (result.get('stop_reason') in ('batch_limit', 'provider_budget', 'time_limit')
            and type(result.get('completed_batches')) is int and result['completed_batches'] > 0
            and result.get('completed_month_pairs', 119) < result.get('total_month_pairs', 119)
            and remaining >= minimum)


def write_resume(path, value):
    if path:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write('resume=' + ('true' if value else 'false') + '\n')
