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
ISSUANCE_CHAIN_REGISTER = Path('ops/forward-reviewed-issuance-chains.csv')
CONTINUITY_REGISTER = Path('ops/forward-reviewed-share-continuity.csv')
BONUS_REGISTER = Path('ops/forward-reviewed-bonus-issues.csv')
SCHEMA = 'v3-forward-krx-share-action-review-2'
MAX_PAIRS_PER_RUN = 5
FIELDS = ('market', 'code', 'name', 'listing_on', 'action', 'shares_before',
          'shares_after', 'new_units', 'old_units', 'notice_url')
NOTICE = re.compile(
    r'https://kind\.krx\.co\.kr/external/\d{4}/\d{2}/\d{2}/\d{6}/\d{14}/\d{5}\.htm\Z'
)
KIND_LISTING_DETAIL = re.compile(
    r'https://kind\.krx\.co\.kr/corpgeneral/stockissuelist\.do\?'
    r'method=searchStockIssueDetail&forward=searchStockIssueDetail&bzProcsNo=(\d{14})&contnId=\1\Z'
)
HOLDER_NEUTRAL_FIELDS = (*FIELDS, 'terms_url')
CONTINUITY_FIELDS = (*FIELDS, 'prior_name')
BONUS_FIELDS = (*FIELDS[:4], *FIELDS[5:], 'ex_on', 'record_on', 'rights_url', 'terms_url')
BONUS_EX_RIGHTS = 'bonus_ex_rights'
BONUS_LISTING = 'bonus_listing'
BONUS_ACTIONS = frozenset({BONUS_EX_RIGHTS, BONUS_LISTING})
KIND_RIGHTS_VIEWER = re.compile(
    r'https://kind\.krx\.co\.kr/common/disclsviewer\.do\?method=search&acptno=\d{14}\Z'
)
DART_TERMS = re.compile(r'https://dart\.fss\.or\.kr/dsaf001/main\.do\?rcpNo=\d{14}\Z')
PRICE_ACTIONS = ('split', 'reverse_split', 'capital_reduction')
COMBINED_BOND_AND_OPTION = 'bond_conversion_and_option_exercise'
SAME_DAY_ISSUANCE_CHAIN = 'same_day_holder_neutral_issuances'
HOLDER_NEUTRAL_ISSUANCES = frozenset({
    'bond_conversion', 'bond_warrant_exercise', 'option_exercise',
    'preferred_to_common', 'third_party_placement', 'successor_share_issuance',
    COMBINED_BOND_AND_OPTION,
})
HOLDER_NEUTRAL_ACTIONS = HOLDER_NEUTRAL_ISSUANCES | {'treasury_cancellation', SAME_DAY_ISSUANCE_CHAIN}
KIND_ISSUANCE_CHAIN_ACTIONS = HOLDER_NEUTRAL_ISSUANCES - {
    COMBINED_BOND_AND_OPTION, 'successor_share_issuance',
}
REQUIRES_TERMS = {'successor_share_issuance', 'treasury_cancellation', COMBINED_BOND_AND_OPTION}
CONTINUITY_ACTIONS = {*PRICE_ACTIONS, 'rename'}
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
    if action == 'rename':
        return before == after and new == old == 1
    if action in HOLDER_NEUTRAL_ACTIONS:
        # The ratio describes the existing holder's shares, not the market float.
        counts_agree = after < before if action == 'treasury_cancellation' else after > before
        return new == old == 1 and counts_agree
    if action not in PRICE_ACTIONS or before == after:
        return False
    ratio = Fraction(new, old)
    exact = before * ratio
    return ratio != 1 and (ratio > 1) == (action == 'split') and exact - 1 < after <= exact


def reviewed_rows(raw):
    reader = csv.DictReader(StringIO(raw.decode('utf-8')))
    expected = {
        FIELDS: PRICE_ACTIONS,
        HOLDER_NEUTRAL_FIELDS: HOLDER_NEUTRAL_ACTIONS - {SAME_DAY_ISSUANCE_CHAIN},
        CONTINUITY_FIELDS: CONTINUITY_ACTIONS,
    }.get(tuple(reader.fieldnames or ()))
    if expected is None:
        raise ShareReviewError('invalid_reviewed_action_header')
    for row in reader:
        code, market, day = row['code'], row['market'], row['listing_on']
        action, listing_url = row['action'], row['notice_url']
        terms = row.get('terms_url') or ''
        name, prior_name = normalized_name(row['name']), normalized_name(row.get('prior_name'))
        try:
            before, after, new, old = (int(row[field]) for field in FIELDS[5:9])
            detailed_listing = KIND_LISTING_DETAIL.fullmatch(listing_url)
            listing_verified = (NOTICE.fullmatch(listing_url)
                                or (action in HOLDER_NEUTRAL_ACTIONS and detailed_listing))
            terms_verified = (KIND_LISTING_DETAIL.fullmatch(terms) if action == COMBINED_BOND_AND_OPTION
                              else NOTICE.fullmatch(terms) or DART_TERMS.fullmatch(terms))
            if (None in row or None in row.values() or not CODE.fullmatch(code) or market not in MARKETS
                    or not name or date.fromisoformat(day).isoformat() != day
                    or (action == 'rename' and (not prior_name or prior_name == name))
                    or (action != 'rename' and row.get('prior_name'))
                    or action not in expected or not listing_verified
                    or (action == COMBINED_BOND_AND_OPTION and not detailed_listing)
                    or not valid_share_terms(action, before, after, new, old)
                    or (action in REQUIRES_TERMS and not terms)
                    or (terms and (terms == listing_url or not terms_verified))):
                raise ValueError()
        except (ValueError, TypeError, ZeroDivisionError):
            raise ShareReviewError('invalid_reviewed_action_record') from None
        row = {**row, 'shares_before': before, 'shares_after': after,
               'new_units': new, 'old_units': old}
        if not terms:
            row.pop('terms_url', None)
        if not prior_name:
            row.pop('prior_name', None)
        yield (day, code), row


def reviewed_actions(raw):
    entries = {}
    for key, row in reviewed_rows(raw):
        if key in entries:
            raise ShareReviewError('duplicate_reviewed_action_record')
        entries[key] = row
    return entries


def reviewed_issuance_chains(raw):
    """Link individually sourced KIND issuances by their exact listed share counts."""
    groups, sources = {}, set()
    for key, row in reviewed_rows(raw):
        source = row['notice_url']
        if (row['action'] not in KIND_ISSUANCE_CHAIN_ACTIONS or 'terms_url' in row
                or not KIND_LISTING_DETAIL.fullmatch(source) or source in sources):
            raise ShareReviewError('invalid_issuance_chain_component')
        sources.add(source)
        groups.setdefault(key, []).append(row)
    entries = {}
    for key, components in groups.items():
        ordered = sorted(components, key=lambda row: row['shares_before'])
        first, last = ordered[0], ordered[-1]
        if (len(ordered) < 2 or any(row['market'] != first['market']
                or normalized_name(row['name']) != normalized_name(first['name']) for row in ordered)
                or any(previous['shares_after'] != following['shares_before']
                       for previous, following in zip(ordered, ordered[1:]))):
            raise ShareReviewError('discontinuous_reviewed_issuance_chain')
        entries[key] = {
            **first, 'action': SAME_DAY_ISSUANCE_CHAIN, 'shares_after': last['shares_after'],
            'listing_notices': [{field: row[field] for field in (
                'action', 'shares_before', 'shares_after', 'notice_url',
            )} for row in ordered],
        }
    return entries


def reviewed_bonus_actions(raw):
    """Create separate entitlement and listing events from one reviewed notice chain."""
    reader = csv.DictReader(StringIO(raw.decode('utf-8')))
    if tuple(reader.fieldnames or ()) != BONUS_FIELDS:
        raise ShareReviewError('invalid_reviewed_bonus_header')
    entries = {}
    for row in reader:
        listing, ex_on, record, code = (row[field] for field in ('listing_on', 'ex_on', 'record_on', 'code'))
        urls = [row[field] for field in ('notice_url', 'rights_url', 'terms_url')]
        try:
            before, after, new, old = (int(row[field]) for field in FIELDS[5:9])
            dates = (ex_on, record, listing)
            if (None in row or None in row.values() or not CODE.fullmatch(code)
                    or row['market'] not in MARKETS or not normalized_name(row['name'])
                    or any(date.fromisoformat(day).isoformat() != day for day in dates)
                    or not ex_on <= record < listing or min(before, old) <= 0 or new <= old
                    or before * new != after * old or len(set(urls)) != 3
                    or not (NOTICE.fullmatch(urls[0]) or KIND_LISTING_DETAIL.fullmatch(urls[0]))
                    or not (NOTICE.fullmatch(urls[1]) or KIND_RIGHTS_VIEWER.fullmatch(urls[1]))
                    or not (NOTICE.fullmatch(urls[2]) or DART_TERMS.fullmatch(urls[2]))):
                raise ValueError()
        except (TypeError, ValueError):
            raise ShareReviewError('invalid_reviewed_bonus_notice') from None
        if (ex_on, code) in entries or (listing, code) in entries:
            raise ShareReviewError('duplicate_reviewed_action_record')
        entries[ex_on, code] = {
            **row, 'action': BONUS_EX_RIGHTS, 'shares_before': before, 'shares_after': before,
            'new_units': new, 'old_units': old, 'notice_url': urls[1],
        }
        entries[listing, code] = {
            **row, 'action': BONUS_LISTING, 'shares_before': before, 'shares_after': after,
            'new_units': 1, 'old_units': 1,
        }
    return entries


def load_forward_actions(root=Path('.')):
    """Load dated evidence for forward replay without changing the historical register."""
    notices = reviewed_actions((root / REGISTER).read_bytes())
    for register, loader in ((HOLDER_NEUTRAL_REGISTER, reviewed_actions),
                             (CONTINUITY_REGISTER, reviewed_actions),
                             (ISSUANCE_CHAIN_REGISTER, reviewed_issuance_chains),
                             (BONUS_REGISTER, reviewed_bonus_actions)):
        path = root / register
        if not path.is_file():
            continue
        added = loader(path.read_bytes())
        if notices.keys() & added.keys():
            raise ShareReviewError('duplicate_reviewed_action_record')
        notices.update(added)
    return notices


def session_review_inputs(notices, day):
    session_notices = {key: row for key, row in notices.items() if key[0] == day}
    payload = json.dumps([session_notices[key] for key in sorted(session_notices)],
                         sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return session_notices, sha256(payload).hexdigest()


def share_transition(previous, current, notice):
    if previous is None:
        return 'first_seen'
    if current is None:
        return 'absent'
    before_name, after_name = (normalized_name(row[1]) for row in (previous, current))
    rename = notice is not None and notice.get('action') == 'rename'
    if not before_name or previous[0] != current[0]:
        return 'identity_changed'
    if rename:
        if (before_name == after_name or before_name != normalized_name(notice.get('prior_name'))
                or after_name != normalized_name(notice.get('name'))):
            return 'identity_changed'
    elif before_name != after_name:
        return 'identity_changed'
    before, after = previous[-1], current[-1]
    if before is None or after is None:
        return 'share_count_unavailable'
    if notice is None:
        return 'unchanged_share_count' if before == after else 'change_needs_notice'
    count_stays_equal = rename or notice.get('action') == BONUS_EX_RIGHTS
    if ((before == after) != count_stays_equal
            or notice['shares_before'] != before or notice['shares_after'] != after
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
            fields = ('terms_url', 'prior_name', 'listing_notices')
            if notice['action'] in BONUS_ACTIONS:
                fields += ('ex_on', 'record_on', 'listing_on', 'rights_url')
            reviews[code].update({field: notice[field] for field in fields if field in notice})
            if new[code][2] == 'traded' and notice['action'] not in BONUS_ACTIONS:
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


def collect_share_reviews(now, get_storage, *, notices=None):
    archived = {}
    for path in sorted(DAILY_STATUS.glob('????-??-??.json')):
        state = json.loads(path.read_bytes())
        if state['status'] == 'archived_privately':
            if state.get('schema') != DAILY_SCHEMA or state.get('date') != path.stem:
                raise ShareReviewError('invalid_daily_status_for_share_review')
            archived[date.fromisoformat(path.stem)] = state
    if len(archived) < 2:
        return
    if notices is None:
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
        session_notices, review_digest = session_review_inputs(notices, day.isoformat())
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
