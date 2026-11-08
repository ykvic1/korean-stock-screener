"""Additive recovery of reported money in missing five-session issuer cohorts.

Original archives and price/directional evidence stay unchanged. Only explicit
reported amounts over all five exact sessions can close an internal gap.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
import argparse
import gzip
import json
import os
from pathlib import Path
import re
import time

from official_probe.clients import KRXClient, KRXProbeError
from v3_krx_money_5day import derive
from v3_krx_money_backfill import CHECKPOINT as HIST, _path, _safe_source_failure_diagnostics
from v3_krx_money_forward import CHECKPOINT as FORWARD
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_calendar_week_money import canonical
from v3_krx_money_week_repair import (
    PRIVATE_REPO, MoneyRepairError, completion_focus, select,
    save_immutable_receipt, save_index, failure_diagnostic,
)

CHECKPOINT = 'derived/money-five-session-repair-checkpoint.json'
PREFIX = 'derived/money-five-session-repairs/'
SCHEMA = 'v3-private-reported-money-five-session-additions-v1'
INDEX_SCHEMA = 'v3-private-reported-money-five-session-repair-index-v1'
MAX_WINDOWS = 12
MAX_CALLS = 144
MAX_BYTES = 600_000


def period_id(start, end):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
        if a.isoformat() != start or b.isoformat() != end or not 4 <= (b-a).days <= 20:
            raise ValueError()
        return start + '_' + end
    except Exception:
        raise MoneyRepairError('invalid_daily_money_repair_period') from None


def repair_path(label):
    if type(label) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', label):
        raise MoneyRepairError('invalid_daily_money_repair_period')
    period_id(*label.split('_'))
    return PREFIX + label + '.json.gz'


def index(raw):
    if raw is None:
        return {'schema': INDEX_SCHEMA, 'windows': {}}
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if (set(data) != {'schema', 'windows'} or data['schema'] != INDEX_SCHEMA
            or type(data['windows']) is not dict or len(data['windows']) > 2000):
            raise ValueError()
        for label, digest in data['windows'].items():
            repair_path(label)
            if type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError()
        return data
    except Exception:
        raise MoneyRepairError('invalid_daily_money_repair_index') from None


def validate(blob, original_blob, original, sessions):
    try:
        if type(blob) is not bytes or not 0 < len(blob) <= MAX_BYTES or len(sessions) != 5:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as handle:
            raw = handle.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError()
        d = json.loads(raw)
        if (set(d) != {'schema', 'period', 'session_dates', 'original_sha256',
                       'amount_factors', 'diagnostic', 'direct_reported_amount_only',
                       'price_or_direction_repaired', 'investment_score_certified'}
            or d['schema'] != SCHEMA
            or d['period'] != period_id(original['window_start'], original['window_end'])
            or d['session_dates'] != sessions
            or d['original_sha256'] != sha256(original_blob).hexdigest()
            or d['direct_reported_amount_only'] is not True
            or d['price_or_direction_repaired'] is not False
            or d['investment_score_certified'] is not False
            or type(d['amount_factors']) is not dict):
            raise ValueError()
        diag = d['diagnostic']
        if (type(diag) is not dict or set(diag) != {'missing_pairs_examined', 'restored_pairs',
                'restored_with_unavailable_price_change', 'restored_with_explicit_zero_amount',
                'unavailable_pairs'}
            or any(type(v) is not int or not 0 <= v <= 20000 for v in diag.values())
            or diag['restored_pairs'] != len(d['amount_factors'])
            or diag['missing_pairs_examined'] != diag['restored_pairs'] + diag['unavailable_pairs']
            or diag['restored_with_unavailable_price_change'] > diag['restored_pairs']
            or diag['restored_with_explicit_zero_amount'] > diag['restored_pairs']):
            raise ValueError()
        for key, f in d['amount_factors'].items():
            if (not re.fullmatch('(KOSPI|KOSDAQ):[0-9]{6}', key)
                or key in original['derived_issuer_money_factors']
                or type(f) is not dict or set(f) != {'total_actual_amount_100k_krw', 'positive_amount_days'}
                or type(f['total_actual_amount_100k_krw']) is not int
                or not 0 <= f['total_actual_amount_100k_krw'] < 5 * 10**12
                or type(f['positive_amount_days']) is not int or not 0 <= f['positive_amount_days'] <= 5
                or (f['positive_amount_days'] == 0 and f['total_actual_amount_100k_krw'] != 0)):
                raise ValueError()
        return d
    except Exception:
        raise MoneyRepairError('invalid_daily_money_repair_receipt') from None


def overlay(store, original_blob, original, sessions, state):
    label = period_id(sessions[0], sessions[-1])
    digest = state['windows'].get(label)
    if digest is None:
        return original, 0
    blob, _ = store.get_content(repair_path(label))
    if blob is None or sha256(blob).hexdigest() != digest:
        raise MoneyRepairError('daily_money_repair_receipt_digest_mismatch')
    additions = validate(blob, original_blob, original, sessions)['amount_factors']
    factors = dict(original['derived_issuer_money_factors'])
    for key, amount in additions.items():
        market, code = key.split(':')
        factors[key] = {**amount, 'market': market, 'issuer_code': code, 'observed_sessions': 5,
            'money_pressure_intrabucket_bp': None, 'late2_vs_early2_money_pressure_bp': None,
            'intrabucket_price_change_bp': None, 'money_amount_is_explicit_acc_trdval': True,
            'five_day_cohort_boundary_price_sign_missing': True,
            'historical_issuer_identity_verified': False, 'regular_session_only_verified': False,
            'corporate_actions_verified': False, 'score_authorized': False}
    return {**original, 'derived_issuer_money_factors': factors,
            'complete_five_session_issuer_market_pairs': len(factors)}, len(additions)


class BudgetClient:
    def __init__(self, client, budget):
        self.client, self.budget = client, budget

    def daily_market(self, market, day):
        for attempt in range(3):
            if self.budget['requests'] >= MAX_CALLS:
                raise MoneyRepairError('daily_money_repair_request_budget_reached')
            self.budget['requests'] += 1
            try:
                return self.client.daily_market(market, day)
            except KRXProbeError as error:
                if attempt == 2 or error.reason not in (
                        'empty_body', 'empty_market_rows', 'transport_failure', 'http_server_error'):
                    raise
                time.sleep(attempt + 1)


def source_additions(client, original_blob, original, sessions, targets, budget):
    fresh, _, _ = derive(BudgetClient(client, budget), sessions)
    reported = fresh['derived_issuer_money_factors']
    if any(key not in reported or reported[key]['total_actual_amount_100k_krw'] !=
           f['total_actual_amount_100k_krw']
           for key, f in original['derived_issuer_money_factors'].items()):
        raise MoneyRepairError('daily_money_repair_established_source_changed')
    additions = {key: {k: reported[key][k] for k in ('total_actual_amount_100k_krw', 'positive_amount_days')}
                 for key in sorted(targets) if key in reported
                 and key not in original['derived_issuer_money_factors']}
    d = {'schema': SCHEMA, 'period': period_id(sessions[0], sessions[-1]),
         'session_dates': sessions, 'original_sha256': sha256(original_blob).hexdigest(),
         'amount_factors': additions, 'diagnostic': {
             'missing_pairs_examined': len(targets), 'restored_pairs': len(additions),
             'restored_with_unavailable_price_change': sum(
                 reported[k]['intrabucket_price_change_bp'] is None for k in additions),
             'restored_with_explicit_zero_amount': sum(reported[k]['positive_amount_days'] < 5 for k in additions),
             'unavailable_pairs': len(targets) - len(additions)},
         'direct_reported_amount_only': True, 'price_or_direction_repaired': False,
         'investment_score_certified': False}
    blob = gzip.compress(canonical(d), compresslevel=9, mtime=0)
    validate(blob, original_blob, original, sessions)
    return d, blob


def repair(snapshot, writer, client, catalog, *, max_windows=MAX_WINDOWS,
           days=None, digest=None, forward_days=None, focus_codes=None):
    from v3_actual_money_participation import read_daily_history
    if (snapshot.repository != PRIVATE_REPO or writer.repository != PRIVATE_REPO
        or type(max_windows) is not int or not 1 <= max_windows <= MAX_WINDOWS):
        raise MoneyRepairError('invalid_daily_money_repair_request')
    snapshot.verify_private()
    before = {p: snapshot.get_content(p) for p in (HIST, FORWARD)}
    raw, _ = snapshot.get_content(CHECKPOINT)
    state = index(raw)
    windows, originals, blobs = read_daily_history(snapshot, asof=catalog['date'], before=before,
        days=days, digest=digest, forward_days=forward_days)
    history, lookups = [], {}
    for sessions, original, blob in zip(windows, originals, blobs):
        merged, _ = overlay(snapshot, blob, original, sessions, state)
        label = period_id(sessions[0], sessions[-1])
        history.append((blob, {**original, 'period': label}, merged))
        lookups[label] = (sessions, original, blob)
    if (any(snapshot.get_content(p) != before[p] for p in before)
        or snapshot.get_content(CHECKPOINT)[0] != raw):
        raise MoneyRepairError('daily_money_repair_snapshot_moved')
    order, targets, _, internal = select(history, catalog, state['windows'], focus_codes,
        period_field='period', factors_field='derived_issuer_money_factors')
    writer.verify_private()
    observed, oldsha = writer.get_content(CHECKPOINT)
    if observed != raw:
        raise MoneyRepairError('daily_money_repair_writer_snapshot_mismatch')
    budget, summaries = {'requests': 0}, []
    for label in order[:max_windows]:
        sessions, original, original_blob = lookups[label]
        if writer.get_content(_path(sessions))[0] != original_blob:
            raise MoneyRepairError('daily_money_repair_writer_original_mismatch')
        blob, _ = writer.get_content(repair_path(label))
        if blob is None:
            # Reserve the maximum three attempts for each of ten requests,
            # ending successfully between cohorts when the run budget is low.
            if MAX_CALLS - budget['requests'] < 30:
                break
            data, blob = source_additions(client, original_blob, original, sessions, targets[label], budget)
            save_immutable_receipt(writer, repair_path(label), blob)
        else:
            data = validate(blob, original_blob, original, sessions)
        updated = {'schema': INDEX_SCHEMA, 'windows': {
            **state['windows'], label: sha256(blob).hexdigest()}}
        encoded = canonical(updated)
        index(encoded)
        oldsha = save_index(writer, encoded, raw, oldsha, checkpoint=CHECKPOINT)
        raw, state = encoded, updated
        summaries.append(data['diagnostic'])
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
                handle.write('daily_money_changed=true\n')
        print(json.dumps({'checkpointed_daily_money_repair': {
            'period_end': sessions[-1], **data['diagnostic']}}), flush=True)
    return {'internal_gap_issuers_before': internal, 'windows_examined': len(summaries),
            'remaining_unexamined_gap_windows': len(order) - len(summaries),
            'source_requests': budget['requests'],
            'restored_issuer_cohorts': sum(s['restored_pairs'] for s in summaries),
            'original_archives_overwritten': False, 'prices_or_directions_repaired': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private-snapshot', required=True)
    p.add_argument('--max-windows', type=int, default=3, choices=range(1, MAX_WINDOWS + 1))
    args = p.parse_args()
    from v3_actual_money_participation import LocalPrivateSnapshot
    catalog = json.loads(Path('docs/v22-market-catalog.json').read_text())
    priority = json.loads(Path('docs/v3-operational-evidence-priority.json').read_text())
    if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
        raise MoneyRepairError('invalid_daily_money_repair_repository')
    token = os.environ['V3_PRIVATE_ARCHIVE_TOKEN']
    result = repair(LocalPrivateSnapshot(args.private_snapshot, token),
        PrivateGitStorage(PRIVATE_REPO, token), KRXClient(os.environ['KRX_API_KEY']), catalog,
        max_windows=args.max_windows, focus_codes=completion_focus(priority, catalog))
    print(json.dumps(result, sort_keys=True))
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            follow = result['windows_examined'] > 0 and result['remaining_unexamined_gap_windows'] > 0
            handle.write('continue_daily_catchup=' + str(follow).lower() + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'diagnostic': (
                          _safe_source_failure_diagnostics(error) or failure_diagnostic(error)),
                          'existing_archives_preserved': True, 'source_payload_logged': False}))
        raise SystemExit(1)
