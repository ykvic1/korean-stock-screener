"""Reconcile daily share-count changes with reviewed KRX listing notices."""
from collections import Counter
import csv
from datetime import date
from fractions import Fraction
from functools import lru_cache
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
import unicodedata

from v3_forward_daily_prices import (
    CODE, MARKETS, OUT as DAILY_STATUS, SCHEMA as DAILY_SCHEMA, completed_sessions, read_daily,
)
from v3_krx_derived_5day import _private_new_blob_readback

OUT = Path('ops/forward-share-action-status')
REGISTER = Path('ops/forward-reviewed-share-actions.csv')
HOLDER_NEUTRAL_REGISTER = Path('ops/forward-reviewed-holder-neutral-actions.csv')
SCHEMA = 'v3-forward-krx-share-action-review-2'
MAX_PAIRS_PER_RUN = 5
FIELDS = ('market', 'code', 'name', 'listing_on', 'action', 'shares_before',
          'shares_after', 'new_units', 'old_units', 'notice_url')
NOTICE = re.compile(
    r'https://kind\.krx\.co\.kr/external/\d{4}/\d{2}/\d{2}/\d{6}/\d{14}/\d{5}\.htm\Z'
)
HOLDER_NEUTRAL_FIELDS = (*FIELDS, 'terms_url')
DART_TERMS = re.compile(r'https://dart\.fss\.or\.kr/dsaf001/main\.do\?rcpNo=\d{14}\Z')
PRICE_ACTIONS = ('split', 'reverse_split', 'capital_reduction')
HOLDER_NEUTRAL_ISSUANCES = frozenset({
    'bond_conversion', 'bond_warrant_exercise', 'option_exercise',
    'preferred_to_common', 'third_party_placement', 'successor_share_issuance',
})
HOLDER_NEUTRAL_ACTIONS = HOLDER_NEUTRAL_ISSUANCES | {'treasury_cancellation'}
REQUIRES_TERMS = {'successor_share_issuance', 'treasury_cancellation'}
STATES = ('unchanged_share_count', 'share_count_unavailable', 'identity_changed',
          'first_seen', 'absent', 'change_needs_notice', 'notice_mismatch', 'notice_matched')


class ShareReviewError(ValueError):
    """Static diagnostics for public workflow logs."""


def normalized_name(value):
    if not isinstance(value, str):
        return ''
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def valid_share_terms(action, before, after, new, old):
    if min(before, after, new, old) <= 0:
        return False
    if action in HOLDER_NEUTRAL_ACTIONS:
        # The ratio describes the existing holder's shares, not the market float.
        counts_agree = after < before if action == 'treasury_cancellation' else after > before
        return new == old == 1 and counts_agree
    if action not in PRICE_ACTIONS or before == after:
        return False
    ratio = Fraction(new, old)
    exact = before * ratio
    return ratio != 1 and (ratio > 1) == (action == 'split') and exact - 1 < after <= exact


def reviewed_actions(raw):
    reader = csv.DictReader(StringIO(raw.decode('utf-8')))
    header = tuple(reader.fieldnames or ())
    if header not in (FIELDS, HOLDER_NEUTRAL_FIELDS):
        raise ShareReviewError('invalid_reviewed_action_header')
    expected = PRICE_ACTIONS if header == FIELDS else HOLDER_NEUTRAL_ACTIONS
    entries = {}
    for row in reader:
        code, market, day = row['code'], row['market'], row['listing_on']
        action, listing_url = row['action'], row['notice_url']
        terms = row.get('terms_url') or ''
        try:
            before, after, new, old = (int(row[field]) for field in FIELDS[5:9])
            if (None in row or None in row.values() or not CODE.fullmatch(code) or market not in MARKETS
                    or not normalized_name(row['name']) or date.fromisoformat(day).isoformat() != day
                    or action not in expected or not NOTICE.fullmatch(listing_url)
                    or not valid_share_terms(action, before, after, new, old)
                    or (action in REQUIRES_TERMS and not terms)
                    or (terms and (terms == listing_url
                                   or not (NOTICE.fullmatch(terms) or DART_TERMS.fullmatch(terms))))):
                raise ValueError()
        except (ValueError, TypeError, ZeroDivisionError):
            raise ShareReviewError('invalid_reviewed_action_record') from None
        key = day, code
        if key in entries:
            raise ShareReviewError('duplicate_reviewed_action_record')
        entries[key] = {**row, 'shares_before': before, 'shares_after': after,
                        'new_units': new, 'old_units': old}
        if not terms:
            entries[key].pop('terms_url', None)
    return entries


def load_forward_actions(root=Path('.')):
    """Load dated evidence for forward replay without changing the historical register."""
    notices = reviewed_actions((root / REGISTER).read_bytes())
    neutral_path = root / HOLDER_NEUTRAL_REGISTER
    if neutral_path.is_file():
        neutral = reviewed_actions(neutral_path.read_bytes())
        if notices.keys() & neutral.keys():
            raise ShareReviewError('duplicate_reviewed_action_record')
        notices.update(neutral)
    return notices


def share_transition(previous, current, notice):
    if previous is None:
        return 'first_seen'
    if current is None:
        return 'absent'
    before_name, after_name = (normalized_name(row[1]) for row in (previous, current))
    if not before_name or before_name != after_name or previous[0] != current[0]:
        return 'identity_changed'
    before, after = previous[-1], current[-1]
    if before is None or after is None:
        return 'share_count_unavailable'
    if before == after:
        return 'notice_mismatch' if notice else 'unchanged_share_count'
    if notice is None:
        return 'change_needs_notice'
    if (notice['shares_before'] != before or notice['shares_after'] != after
            or notice['market'] != current[0] or normalized_name(notice['name']) != after_name):
        return 'notice_mismatch'
    return 'notice_matched'


def reconcile(before, after, notices, notice_sha):
    earlier, day = before['date'], after['date']
    if earlier >= day:
        raise ShareReviewError('invalid_share_review_date_order')
    old, new = before['rows'], after['rows']
    counts = Counter()
    reviews = {}
    unseen_notices = 0
    for code in sorted(old.keys() | new.keys()):
        notice = notices.get((day, code))
        state = share_transition(old.get(code), new.get(code), notice)
        counts[state] += 1
        if state in ('unchanged_share_count', 'share_count_unavailable') and notice is None:
            continue
        reviews[code] = {'state': state}
        if state == 'notice_matched':
            reviews[code].update({key: notice[key] for key in (
                'action', 'new_units', 'old_units', 'notice_url',
            )})
            if 'terms_url' in notice:
                reviews[code]['terms_url'] = notice['terms_url']
            if new[code][2] == 'traded':
                # Keep KRW exact for ratios such as 1-for-10. The four values
                # represent the event session in pre-event share units.
                ratio = Fraction(notice['new_units'], notice['old_units'])
                reviews[code]['ohlc_krw_per_previous_share'] = [
                    str(value * ratio) for value in new[code][3:7]
                ]
    for notice_day, code in notices:
        if notice_day == day and code not in old and code not in new:
            unseen_notices += 1
    public = {
        'schema': SCHEMA, 'previous_session': earlier, 'session': day,
        'status': 'adjacent_share_counts_reviewed',
        'previous_source_sha256': before['source_sha256'],
        'current_source_sha256': after['source_sha256'],
        'reviewed_session_notices_sha256': notice_sha,
        'compared_distinct_codes': sum(counts.values()),
        'states': {state: counts[state] for state in STATES},
        'notice_codes_absent_from_both_panels': unseen_notices,
        'full_corporate_action_history_certified': False,
        'performance_validated': False,
    }
    return public, reviews


def collect_share_reviews(now, get_storage):
    archived = {}
    for path in sorted(DAILY_STATUS.glob('????-??-??.json')):
        state = json.loads(path.read_bytes())
        if state['status'] == 'archived_privately':
            if state.get('schema') != DAILY_SCHEMA or state.get('date') != path.stem:
                raise ShareReviewError('invalid_daily_status_for_share_review')
            archived[date.fromisoformat(path.stem)] = state
    if len(archived) < 2:
        return
    notices = load_forward_actions()
    days = [date.fromisoformat(day)
            for day in completed_sessions(min(archived), max(archived), now)]

    @lru_cache(maxsize=MAX_PAIRS_PER_RUN + 1)
    def panel(day):
        saved = read_daily(day.isoformat(), get_storage())
        if saved is None or saved['source_sha256'] != archived[day]['source_sha256']:
            raise ShareReviewError('share_review_daily_source_mismatch')
        return saved

    processed = 0
    for earlier, day in zip(days, days[1:]):
        if earlier not in archived or day not in archived:
            continue
        session_notices = {key: value for key, value in notices.items()
                           if key[0] == day.isoformat()}
        notice_bytes = json.dumps([session_notices[key] for key in sorted(session_notices)],
                                  sort_keys=True, ensure_ascii=False,
                                  separators=(',', ':')).encode()
        review_digest = sha256(notice_bytes).hexdigest()
        target = OUT / f'{day}.json'
        if target.exists():
            previous = json.loads(target.read_bytes())
            if (previous.get('schema') == SCHEMA
                    and previous.get('reviewed_session_notices_sha256') == review_digest
                    and previous.get('previous_session') == earlier.isoformat()
                    and previous.get('previous_source_sha256') == archived[earlier]['source_sha256']
                    and previous.get('current_source_sha256') == archived[day]['source_sha256']):
                continue
        if processed >= MAX_PAIRS_PER_RUN:
            break
        processed += 1
        public, cases = reconcile(panel(earlier), panel(day), session_notices, review_digest)
        expected = json.dumps({'summary': public, 'cases': cases},
                              ensure_ascii=False, separators=(',', ':')).encode()
        # Version the complete result so corrected sources preserve earlier evidence.
        result_digest = sha256(expected).hexdigest()
        path = f'derived/forward-share-actions/{day}/{result_digest}.json'
        storage = get_storage()
        old, _ = storage.get_content(path)
        if old is None:
            storage.put_new(path, expected, 'Archive reviewed share transitions for ' + day.isoformat())
            _private_new_blob_readback(storage, path, expected)
        elif old != expected:
            raise ShareReviewError('existing_share_review_differs')
        OUT.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(public, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(public))
