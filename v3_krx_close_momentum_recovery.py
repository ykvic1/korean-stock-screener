"""Restore missing 121-session momentum facts from official KRX closing prices."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from official_probe.clients import KRXClient, ProbeError
from official_probe.validation import _numeric
from v3_dated_market_roster import DatedRosterError, load_dated_listing
from v3_fdr_cache_krx_close_corroboration import cache_close_index
from v3_krx_history_collection import MARKETS, _snapshot_market
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_saved_close_crosscheck import safe_failure_reason
from v3_krx_session_candidates import candidate_dates
from v3_krx_money_forward import RetryingMoneyClient
from v3_path_provisional_factors import CLOSE_PATH_SCHEMA, MOMENTUM_MAX
from v3_unscored_path_observations import close_path_facts

OUTPUT = 'v3-krx-close-momentum-recovery.json'
STOCK_FIELD = 'v3_krx_close_momentum_path'
SESSIONS = 121
MAX_TARGETS = 100
NOTICE_FILE = Path(__file__).with_name('v3_krx_face_consolidation_notices.json')
NOTICE_SCHEMA = 'v3-krx-kind-fixed-face-consolidation-v1'
KIND_DOCUMENT = r'https://kind\.krx\.co\.kr/external/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{6}/[0-9]{14}/'
SYNDICATED_DISCLOSURE = r'https://(?:stock\.mk\.co\.kr/news/disclosure/template|spn\.stockplus\.com/news/api/v1/disclosure_views/koscom)/[0-9]+'


def select_targets(market, report, ledger):
    day = market['date']
    stocks = market['stocks']
    if (report['asof'] != day or ledger['market_asof'] != day
            or len({s['code'] for s in stocks}) != len(stocks)):
        raise ValueError('unaligned_close_momentum_sources')
    targets = {}
    for s in stocks:
        code = s['code']
        row = report['rows'][code]
        factor = row['parts']['momentum'].get('existing_momentum')
        baseline = ledger['rows'][code]['factors'].get('momentum')
        if (s.get('price_date') == day and s['market'] in MARKETS
            and 90-MOMENTUM_MAX <= row['available'] < 100
            and factor is not None and factor[1:] == [8, day]
            and baseline is not None and baseline['available'] == 8
            and baseline['asof'] == day
            and (s.get(STOCK_FIELD) or {}).get('asof') != day):
            targets[(s['market'], code)] = s
    if len(targets) > MAX_TARGETS:
        raise ValueError('close_momentum_candidate_budget_exceeded')
    return targets


def closes_on(client, market, day, codes, *, missing=None, openings=None):
    rows, raw = client.daily_market(market, day)
    _snapshot_market(rows, raw, market=market, asof=day)
    found, rejected = {}, {}
    for row in rows:
        code = row['ISU_CD']
        if code not in codes:
            continue
        # This receipt uses close and prior-close continuity only. A missing
        # open/high/low or volume does not invalidate a reported close.
        try:
            close = _numeric(row.get('TDD_CLSPRC'), 'TDD_CLSPRC', positive=True)
        except ProbeError:
            rejected[code] = 'invalid_close'
            continue
        try:
            shares = _numeric(row.get('LIST_SHRS'), 'LIST_SHRS', positive=True)
        except ProbeError:
            rejected[code] = 'invalid_listed_shares'
            continue
        change = str(row.get('CMPPREVDD_PRC', '')).strip().replace(',', '')
        reference = close-int(change) if re.fullmatch(r'[+-]?[0-9]+', change) else None
        found[code] = (close, shares, reference)
        if openings is not None and code in openings:
            try:
                openings[code] = _numeric(row.get('TDD_OPNPRC'), 'TDD_OPNPRC', positive=True)
            except ProbeError:
                pass  # The auction link stays unavailable without a positive open.
    if missing is not None:
        missing.update(rejected.get(code, 'absent_issuer') for code in codes if code not in found)
    return found


def valid_face_notice_source(item):
    """Recognize exchange notices and the syndicated Koscom disclosure copy."""
    kind = KIND_DOCUMENT
    if 'kind_url' in item:
        return bool(re.fullmatch(kind + r'70795\.htm', item['kind_url']))
    if 'listing_notice_url' in item:
        if 'reference_krw' in item:
            reference_form = (r'99321\.htm' if item['new_face_krw'] > item['old_face_krw']
                              else r'99322\.htm')
            return (item.get('market') == 'KOSPI'
                    and bool(re.fullmatch(kind + reference_form, item.get('notice_url', '')))
                    and bool(re.fullmatch(kind + r'68155\.htm', item['listing_notice_url']))
                    and all(type(item.get(k)) is int and item[k] > 0 for k in
                            ('listed_shares_before', 'listed_shares_after'))
                    and (item['listed_shares_before'] * item['old_face_krw']
                         == item['listed_shares_after'] * item['new_face_krw']))
        return (item.get('market') == 'KOSPI'
                and bool(re.fullmatch(kind + r'68155\.htm', item['listing_notice_url']))
                and all(type(item.get(k)) is int and item[k] > 0 for k in
                        ('listed_shares_before', 'listed_shares_after'))
                and item['listed_shares_before'] > item['listed_shares_after']
                and 'reference_krw' not in item)
    url = item.get('notice_url', '')
    if item.get('market') == 'KOSPI':
        return bool(re.fullmatch(kind + r'99321\.htm', url)
                    and item.get('face_evidence_url', '').startswith('https://'))
    if item.get('market') == 'KOSDAQ':
        return bool(re.fullmatch(
            r'https://spn\.stockplus\.com/news/api/v1/disclosure_views/koscom/[0-9]+', url))
    return False


def load_face_notices(path=NOTICE_FILE):
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get('schema') != NOTICE_SCHEMA or type(data.get('notices')) is not dict:
        raise ValueError('invalid_krx_face_consolidation_notices')
    for code, records in data['notices'].items():
        if not re.fullmatch(r'[0-9A-Z]{6}', code) or type(records) is not dict:
            raise ValueError('invalid_krx_face_consolidation_notice_identity')
        for day, item in records.items():
            if (date.fromisoformat(day).isoformat() != day or type(item) is not dict
                or any(type(item.get(k)) is not int or item[k] <= 0
                       for k in ('old_face_krw', 'new_face_krw'))
                or (('listing_notice_url' not in item or 'reference_krw' in item)
                    and (type(item.get('reference_krw')) is not int or item['reference_krw'] <= 0))
                or item['new_face_krw'] == item['old_face_krw']
                or (item['new_face_krw'] < item['old_face_krw']
                    and ('listing_notice_url' not in item or 'reference_krw' not in item))
                or not valid_face_notice_source(item)):
                raise ValueError('invalid_krx_face_consolidation_notice')
    return data['notices'], sha256(raw).hexdigest()


def load_bonus_notices(path=NOTICE_FILE):
    data = json.loads(path.read_bytes())
    if data.get('schema') != NOTICE_SCHEMA:
        raise ValueError('invalid_krx_bonus_issue_notices')
    notices = data.get('bonus_issues', {})
    if type(notices) is not dict:
        raise ValueError('invalid_krx_bonus_issue_notices')
    for code, records in notices.items():
        if not re.fullmatch(r'[0-9A-Z]{6}', code) or type(records) is not dict:
            raise ValueError('invalid_krx_bonus_issue_notice_identity')
        for day, item in records.items():
            if (type(item) is not dict or date.fromisoformat(day).isoformat() != day
                or item.get('market') not in MARKETS
                or any(type(item.get(k)) is not int or item[k] <= 0 for k in (
                    'reference_krw', 'ratio_numerator', 'ratio_denominator',
                    'shares_before', 'shares_after'))
                or item['shares_after'] <= item['shares_before']
                or date.fromisoformat(item['listing_date']).isoformat() != item['listing_date']
                or item['listing_date'] <= day
                or not re.fullmatch(KIND_DOCUMENT + (
                    r'70795\.htm' if 'consolidation_ratio' in item else r'70766\.htm'),
                    item.get('rights_url', ''))
                or any(not item.get(k, '').startswith('https://') for k in ('ratio_url', 'listing_url'))):
                raise ValueError('invalid_krx_bonus_issue_notice')
            if 'consolidation_ratio' in item and (
                item['market'] != 'KOSDAQ'
                or any(type(item.get(k)) is not int or item[k] <= 0 for k in
                       ('consolidation_ratio', 'shares_before_consolidation', 'bonus_eligible_shares'))
                or item['bonus_eligible_shares'] > item['shares_before']
                or ((item['shares_after'] - item['shares_before']) * item['ratio_denominator']
                    != item['bonus_eligible_shares'] * item['ratio_numerator'])
                or item['consolidation_ratio'] <= 1
                or abs(item['shares_before_consolidation']
                       - item['shares_before'] * item['consolidation_ratio'])
                    >= item['consolidation_ratio']
                or not re.fullmatch(KIND_DOCUMENT + r'70764\.htm',
                                    item.get('consolidation_url', ''))):
                raise ValueError('invalid_krx_consolidation_bonus_notice')
    return notices


def krx_tick(reference):
    return next(tick for upper, tick in ((2_000, 1), (5_000, 5),
                (20_000, 10), (50_000, 50), (200_000, 100),
                (500_000, 500), (float('inf'), 1_000)) if reference < upper)


def verified_bonus_issue(observations, sessions, index, notice):
    """Match the rights-date reference and the separate later share issuance."""
    previous_close, previous_shares, _ = observations[index - 1]
    _, rights_shares, reference = observations[index]
    if notice is None or reference != notice['reference_krw']:
        return False
    if 'consolidation_ratio' in notice:
        if (previous_shares != notice['shares_before_consolidation']
                or rights_shares != notice['shares_before']):
            return False
    elif not 0 < reference < previous_close or (
            previous_shares != notice['shares_before'] or rights_shares != previous_shares):
        return False
    listing_date = notice['listing_date']
    if listing_date not in sessions[index + 1:]:
        return False
    listing = sessions.index(listing_date, index + 1)
    if (observations[listing - 1][1] != rights_shares or
            observations[listing][1] != notice['shares_after']):
        return False
    if 'consolidation_ratio' in notice:
        # A treasury-share exclusion changes the effective bonus denominator.
        pre_bonus = previous_close * notice['consolidation_ratio'] * rights_shares
        post_bonus_shares = notice['shares_after']
        return abs(pre_bonus - reference * post_bonus_shares) <= krx_tick(reference) * post_bonus_shares
    denominator = notice['ratio_denominator']
    enlarged = denominator + notice['ratio_numerator']
    return abs(previous_close * denominator - reference * enlarged) <= krx_tick(reference) * enlarged


def verified_face_consolidation(previous, current, notice):
    """Check the announced fixed reference against both sides of the action."""
    previous_close, previous_shares, _ = previous
    _, shares, reference = current
    if notice is None or previous_close <= 0 or reference is None or reference <= 0:
        return False
    old_face, new_face = notice['old_face_krw'], notice['new_face_krw']
    if new_face > old_face:
        if reference <= previous_close or shares >= previous_shares:
            return False
    elif reference >= previous_close or shares <= previous_shares:
        return False
    if 'listing_notice_url' in notice:
        if previous_shares != notice['listed_shares_before'] or shares != notice['listed_shares_after']:
            return False
        if 'reference_krw' not in notice:
            # Without a separately published reference, require exact price conversion.
            return (abs(previous_shares * old_face - shares * new_face) < new_face
                    and previous_close * new_face == reference * old_face)
        if previous_shares * old_face != shares * new_face:
            return False
    if reference != notice['reference_krw']:
        return False
    # The published post-action count can truncate fractional shares.
    if abs(previous_shares * old_face - shares * new_face) >= new_face:
        return False
    # KRX posts a tick-rounded reference on the effective trading date.
    return abs(previous_close * new_face - reference * old_face) <= krx_tick(reference) * old_face


def load_auction_notices(path=NOTICE_FILE):
    data = json.loads(path.read_bytes())
    if data.get('schema') != NOTICE_SCHEMA:
        raise ValueError('invalid_krx_auction_capital_reduction_notices')
    notices = data.get('auction_capital_reductions', {})
    if type(notices) is not dict:
        raise ValueError('invalid_krx_auction_capital_reduction_notices')
    for code, records in notices.items():
        if not re.fullmatch(r'[0-9A-Z]{6}', code) or type(records) is not dict:
            raise ValueError('invalid_krx_auction_notice_identity')
        for day, notice in records.items():
            if (date.fromisoformat(day).isoformat() != day or type(notice) is not dict
                or notice.get('market') not in MARKETS
                or any(type(notice.get(k)) is not int or notice[k] <= 0 for k in (
                    'valuation_krw', 'lower_krw', 'upper_krw', 'shares_before', 'shares_after', 'ratio'))
                or not notice['lower_krw'] < notice['valuation_krw'] < notice['upper_krw']):
                raise ValueError('invalid_krx_auction_capital_reduction_notice')
            if 'split_ratio' in notice:
                if (type(notice['split_ratio']) is not int or notice['split_ratio'] <= 1
                        or type(notice.get('shares_cancelled')) is not int
                        or not 0 <= notice['shares_cancelled'] < notice['shares_before']
                        or (notice['shares_before'] - notice['shares_cancelled']) * notice['split_ratio']
                            != notice['shares_after'] * notice['ratio']
                        or not re.fullmatch(KIND_DOCUMENT + r'11309\.htm',
                                            notice.get('action_url', ''))):
                    raise ValueError('invalid_krx_auction_split_notice')
            elif (notice['ratio'] <= 1
                    or abs(notice['shares_before'] - notice['shares_after'] * notice['ratio'])
                        >= notice['ratio']):
                raise ValueError('invalid_krx_auction_reduction_ratio')
            if notice['market'] == 'KOSPI' and 'split_ratio' not in notice:
                auction_doc = KIND_DOCUMENT + r'99328\.htm'
                listing_doc = KIND_DOCUMENT + r'68155\.htm'
                if not re.fullmatch(KIND_DOCUMENT + r'(?:11306|99667)\.htm',
                                    notice.get('action_url', '')):
                    raise ValueError('invalid_krx_auction_reduction_source')
            elif notice['market'] == 'KOSPI':
                auction_doc = KIND_DOCUMENT + r'99326\.htm|' + SYNDICATED_DISCLOSURE
                listing_doc = SYNDICATED_DISCLOSURE
            else:
                auction_doc = KIND_DOCUMENT + r'70793\.htm|' + SYNDICATED_DISCLOSURE
                listing_doc = KIND_DOCUMENT + r'70763\.htm|' + SYNDICATED_DISCLOSURE
            if (not re.fullmatch(auction_doc, notice.get('auction_url', ''))
                    or not re.fullmatch(listing_doc, notice.get('listing_url', ''))):
                raise ValueError('invalid_krx_auction_notice_source')
    return notices


def verified_auction_capital_reduction(previous, current, opening, notice):
    """Confirm the share action and the separately observed auction opening."""
    if notice is None:
        return False
    previous_close, previous_shares, _ = previous
    close, shares, reference = current
    split_ratio = notice.get('split_ratio', 1)
    valuation = notice['valuation_krw']
    return (type(opening) is int and opening == reference
            and notice['lower_krw'] <= opening <= notice['upper_krw']
            and 100 * abs(close - opening) <= 30 * opening
            and previous_shares == notice['shares_before'] and shares == notice['shares_after']
            and abs(previous_close * notice['ratio'] - valuation * split_ratio)
                <= krx_tick(valuation) * split_ratio)


def load_resumption_notices(path=NOTICE_FILE):
    data = json.loads(path.read_bytes())
    notices = data.get('trading_resumptions', {})
    if data.get('schema') != NOTICE_SCHEMA or type(notices) is not dict:
        raise ValueError('invalid_krx_trading_resumption_notices')
    kiwoom_notice = r'https://invest\.kiwoom\.com/inv/disclosure_detail\?code=02&date=[^&]+&keyword=[^&]+&seq=[0-9]+&stockCode=&tp=1&type=02'
    for code, records in notices.items():
        if not re.fullmatch(r'[0-9A-Z]{6}', code) or type(records) is not dict:
            raise ValueError('invalid_krx_trading_resumption_notice_identity')
        for day, item in records.items():
            if (date.fromisoformat(day).isoformat() != day or type(item) is not dict
                    or item.get('market') not in MARKETS
                    or any(type(item.get(k)) is not int or item[k] <= 0 for k in (
                        'valuation_krw', 'lower_krw', 'upper_krw'))
                    or not item['lower_krw'] < item['valuation_krw'] < item['upper_krw']):
                raise ValueError('invalid_krx_trading_resumption_notice')
            pattern = (KIND_DOCUMENT + r'99409\.htm' if item['market'] == 'KOSPI'
                       else KIND_DOCUMENT + r'70793\.htm|' + kiwoom_notice)
            if not re.fullmatch(pattern, item.get('notice_url', '')):
                raise ValueError('invalid_krx_trading_resumption_source')
    return notices


def verified_trading_resumption(previous, current, opening, notice):
    """Keep the market return after a verified auction with unchanged shares."""
    previous_close, previous_shares, _ = previous
    close, shares, reference = current
    return (notice is not None and type(opening) is int and opening == reference
            and previous_shares == shares and previous_close == notice['valuation_krw']
            and notice['lower_krw'] <= opening <= notice['upper_krw']
            and 100 * abs(close - opening) <= 30 * opening)


def make_receipt(observations, *, asof, sessions=None, face_notices=None,
                 bonus_notices=None, auction_notices=None, auction_openings=None,
                 resumption_notices=None):
    if len(observations) != SESSIONS or any(row is None for row in observations):
        return 'incomplete_close_history', None
    if (face_notices or bonus_notices or auction_notices or resumption_notices) and (
            sessions is None or len(sessions) != SESSIONS or sessions[-1] != asof
            or list(sessions) != sorted(set(sessions))):
        raise ValueError('invalid_close_momentum_session_window')
    links = {}
    verified_jump_indices = set()
    link_counts = Counter()
    for i, (previous, current) in enumerate(zip(observations, observations[1:]), start=1):
        previous_close, previous_shares, _ = previous
        _, shares, reference = current
        if reference == previous_close:
            continue
        notice = face_notices.get(sessions[i]) if face_notices else None
        if reference is not None and verified_face_consolidation(previous, current, notice):
            links[i] = Fraction(reference, previous_close)
            if notice['new_face_krw'] < notice['old_face_krw']:
                link_counts['kind_fixed_face_split_links'] += 1
            else:
                link_counts['kind_fixed_face_consolidation_links'] += 1
            continue
        bonus = bonus_notices.get(sessions[i]) if bonus_notices else None
        if reference is not None and verified_bonus_issue(observations, sessions, i, bonus):
            links[i] = Fraction(reference, previous_close)
            link_counts['kind_bonus_issue_links'] += 1
            continue
        auction = auction_notices.get(sessions[i]) if auction_notices else None
        opening = (auction_openings or {}).get(sessions[i]) if auction else None
        if verified_auction_capital_reduction(previous, current, opening, auction):
            # Scale by the announced pro-rata exchange ratio. Comparing to
            # the auction open here would erase the opening price movement.
            links[i] = Fraction(auction['ratio'], auction.get('split_ratio', 1))
            verified_jump_indices.add(i)
            link_counts['kind_auction_capital_reduction_links'] += 1
            continue
        resumption = resumption_notices.get(sessions[i]) if resumption_notices else None
        opening = (auction_openings or {}).get(sessions[i]) if resumption else None
        if verified_trading_resumption(previous, current, opening, resumption):
            verified_jump_indices.add(i)
            link_counts['kind_trading_resumption_links'] += 1
            continue
        if previous_shares != shares:
            return 'listed_share_change_reference_discontinuous_or_missing', None
        if reference is None:
            return 'missing_official_previous_close_reference', None
        return 'official_reference_discontinuity', None

    # Rebase observations preceding each confirmed action to the latest
    # price basis. The latest close is unchanged; the resumption-day move
    # keeps auction price discovery when the share ratio is independently known.
    multiplier = Fraction(1)
    closes = []
    for i in range(SESSIONS - 1, -1, -1):
        closes.append(float(observations[i][0] * multiplier))
        if i in links:
            multiplier *= links[i]
    facts = close_path_facts(closes[::-1], verified_jump_indices=verified_jump_indices)
    if facts is None:
        return 'invalid_or_discontinuous_close_history', None
    receipt = {'schema': CLOSE_PATH_SCHEMA, 'asof': asof,
               'path_window_trading_sessions': SESSIONS, **facts}
    receipt.update(link_counts)
    return 'recovered', receipt


def close_anchor(stock, official_row, dated_close):
    """Keep the saved price intact and anchor this independent close path."""
    if official_row is None:
        return None
    official_close = official_row[0]
    if dated_close == official_close:
        return 'dated_fdr_krx_match'
    if stock['price'] == official_close:
        return 'saved_close_krx_match'
    return None


def run(market, report, ledger, key, *, load_listing=load_dated_listing,
        face_notices=None, bonus_notices=None, auction_notices=None, resumption_notices=None):
    day = market['date']
    targets = select_targets(market, report, ledger)
    start = (date.fromisoformat(day) - timedelta(days=210)).isoformat()
    dates = candidate_dates(calendar_weekday_candidates(start, day), source='xkrx')['dates'][-SESSIONS:]
    if len(dates) != SESSIONS or dates[-1] != day:
        raise ValueError('invalid_close_momentum_session_window')

    cache, cache_sha = {}, None
    if targets:
        try:
            frame, cache_sha = load_listing(day)
        except DatedRosterError:
            pass  # Existing same-close recovery can still run.
        else:
            cache = cache_close_index(frame)

    def collect(exchange):
        wanted = {code: s for (m, code), s in targets.items() if m == exchange}
        counts = Counter()
        if not wanted:
            return counts, {}, 0
        client = RetryingMoneyClient(KRXClient(key))
        try:
            auction_codes = {}
            for code in wanted:
                for records in (auction_notices, resumption_notices):
                    for session, notice in (records or {}).get(code, {}).items():
                        if notice['market'] == exchange:
                            auction_codes.setdefault(session, {})[code] = None
            current = closes_on(client, exchange, day, wanted, openings=auction_codes.get(day))
            anchors = {code: close_anchor(stock, current.get(code), cache.get((exchange, code)))
                       for code, stock in wanted.items()}
            eligible = {code for code, anchor in anchors.items() if anchor is not None}
            counts['current_close_or_basis_unmatched'] = len(wanted) - len(eligible)
            for code in eligible:
                counts[f'eligible_{anchors[code]}'] += 1
            if not eligible:
                return counts, {}, client.requests
            history = []
            for session in dates[:-1]:
                missing = Counter()
                history.append(closes_on(client, exchange, session, eligible, missing=missing,
                                         openings=auction_codes.get(session)))
                for reason, count in missing.items():
                    counts[f'history_rows_{reason}'] += count
                if missing:
                    counts['history_sessions_with_missing_rows'] += 1
            history.append(current)
            receipts = {}
            for code in sorted(eligible):
                notices = {
                    session: notice for session, notice in (face_notices or {}).get(code, {}).items()
                    if notice.get('market', 'KOSDAQ') == exchange
                }
                bonus = {
                    session: notice for session, notice in (bonus_notices or {}).get(code, {}).items()
                    if notice['market'] == exchange
                }
                resumptions = {
                    session: notice for session, notice in (resumption_notices or {}).get(code, {}).items()
                    if notice['market'] == exchange
                }
                outcome, receipt = make_receipt([d.get(code) for d in history],
                                                asof=day, sessions=dates,
                                                face_notices=notices, bonus_notices=bonus,
                                                resumption_notices=resumptions,
                                                auction_notices={
                                                    session: notice for session, notice in
                                                    (auction_notices or {}).get(code, {}).items()
                                                    if notice['market'] == exchange
                                                },
                                                auction_openings={session: openings.get(code)
                                                                  for session, openings in auction_codes.items()})
                counts[outcome] += 1
                if receipt is not None:
                    if receipt.get('kind_fixed_face_consolidation_links'):
                        counts['recovered_with_kind_face_consolidation'] += 1
                    if receipt.get('kind_fixed_face_split_links'):
                        counts['recovered_with_kind_face_split'] += 1
                    if receipt.get('kind_bonus_issue_links'):
                        counts['recovered_with_kind_bonus_issue'] += 1
                    if receipt.get('kind_auction_capital_reduction_links'):
                        counts['recovered_with_kind_auction_capital_reduction'] += 1
                    if receipt.get('kind_trading_resumption_links'):
                        counts['recovered_with_kind_trading_resumption'] += 1
                    anchor = anchors[code]
                    counts[f'recovered_{anchor}'] += 1
                    if current[code][0] != wanted[code]['price']:
                        counts['recovered_despite_saved_close_mismatch'] += 1
                    receipts[(exchange, code)] = {**receipt, 'close_anchor': anchor}
            return counts, receipts, client.requests
        finally:
            client.client.session.close()

    counts = Counter()
    calls = 0
    with ThreadPoolExecutor(max_workers=len(MARKETS)) as pool:
        for reasons, receipts, requests in pool.map(collect, MARKETS):
            counts.update(reasons)
            calls += requests
            for identity, receipt in receipts.items():
                targets[identity][STOCK_FIELD] = receipt
    return {'asof': day, 'first_source_session': dates[0], 'session_count': SESSIONS,
            'selected': len(targets), 'counts': dict(counts), 'official_requests': calls,
            'dated_fdr_close_source_sha256': cache_sha,
            'dated_fdr_close_requests': int(bool(targets)),
            'raw_source_exported': False, 'volume_evidence_used': False,
            'corporate_actions_adjusted_verified': False}


def main():
    root = Path('docs')
    raw = (root/'data.json').read_bytes()
    market = json.loads(raw)
    report = json.loads((root/'v3-operational-research-scores.json').read_bytes())
    ledger_name = 'v3-issuer-last-observed-factors.json'
    ledger_raw = (root/ledger_name).read_bytes()
    for name, payload in (('data.json', raw), (ledger_name, ledger_raw)):
        if sha256(payload).hexdigest() != report['source_hashes'].get(name):
            raise ValueError('unaligned_close_momentum_sources')
    ledger = json.loads(ledger_raw)
    notices, notice_digest = load_face_notices()
    bonus_notices = load_bonus_notices()
    auction_notices = load_auction_notices()
    resumption_notices = load_resumption_notices()
    result = run(market, report, ledger, os.environ.get('KRX_API_KEY', ''),
                 face_notices=notices, bonus_notices=bonus_notices, auction_notices=auction_notices,
                 resumption_notices=resumption_notices)
    result['kind_trading_resumption_notice_count'] = sum(map(len, resumption_notices.values()))
    result['kind_auction_capital_reduction_notice_count'] = sum(map(len, auction_notices.values()))
    result['kind_bonus_issue_notice_count'] = sum(map(len, bonus_notices.values()))
    result['kind_face_consolidation_notice_count'] = sum(map(len, notices.values()))
    result['kind_face_consolidation_notice_sha256'] = notice_digest
    result['input_market_sha256'] = sha256(raw).hexdigest()
    if result['counts'].get('recovered'):
        (root/'data.json').write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False),
                                     encoding='utf-8')
    text = json.dumps(result, sort_keys=True, allow_nan=False)
    (root/OUTPUT).write_text(text+'\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'close_momentum_recovery_blocked',
                          'reason':safe_failure_reason(error)}))
        raise SystemExit(1)
