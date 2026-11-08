"""Bounded, additive repair of private weekly *reported amount* observations.

Original archives are immutable. A sidecar adds only missing issuer weeks,
after all calendar sessions have explicit ACC_TRDVAL and established amounts
agree with a fresh official read. No OHLC, price sign, halt reason or raw daily
amount is inferred or retained. Nothing here certifies an investment score.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import argparse
import gzip
import json
import os
from pathlib import Path
import re
import time

from official_probe.clients import KRXClient, ProbeError, KRXProbeError
from official_probe.validation import _numeric
from v3_krx_history_collection import _snapshot_market
from v3_krx_daily_anomaly_triage import classify_row
from v3_krx_calendar_week_money import (
    CHECKPOINT as WEEKLY, HISTORY_WEEKS, manifest, path as week_path,
    week_days, validate as validate_week, canonical,
)
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError, storage_diagnostic
from v3_krx_derived_5day import _private_new_blob_readback
from v3_dart_historical_event_receipts import _save_cursor

PRIVATE_REPO = 'ykvic1/korean-stock-private-data'
CHECKPOINT = 'derived/money-calendar-week-repair-checkpoint.json'
PREFIX = 'derived/money-calendar-week-repairs/'
SCHEMA = 'v3-private-reported-money-week-additions-v1'
INDEX_SCHEMA = 'v3-private-reported-money-week-repair-index-v1'
MAX_WEEKS = 24
MAX_CALLS = 10 * MAX_WEEKS
MAX_BYTES = 600_000


class MoneyRepairError(ValueError):
    """Static diagnostics only."""


def repair_path(week):
    day = date.fromisoformat(week)
    if day.isoformat() != week or day.weekday() != 6:
        raise MoneyRepairError('invalid_money_repair_week')
    return PREFIX + week + '.json.gz'


def index(raw):
    if raw is None:
        return {'schema': INDEX_SCHEMA, 'weeks': {}}
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if set(data) != {'schema', 'weeks'} or data['schema'] != INDEX_SCHEMA:
            raise ValueError()
        if type(data['weeks']) is not dict or len(data['weeks']) > 2000:
            raise ValueError()
        for week, digest in data['weeks'].items():
            repair_path(week)
            if type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError()
        return data
    except Exception:
        raise MoneyRepairError('invalid_money_repair_index') from None


def validate(blob, original_blob, original):
    try:
        if type(blob) is not bytes or not 0 < len(blob) <= MAX_BYTES:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as handle:
            raw = handle.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError()
        d = json.loads(raw)
        if (set(d) != {'schema', 'week_end', 'session_dates', 'original_sha256',
                      'factors', 'diagnostic', 'direct_reported_amount_only',
                      'price_or_direction_repaired', 'investment_score_certified'}
            or d['schema'] != SCHEMA or d['week_end'] != original['week_end']
            or d['session_dates'] != original['session_dates']
            or d['original_sha256'] != sha256(original_blob).hexdigest()
            or d['direct_reported_amount_only'] is not True
            or d['price_or_direction_repaired'] is not False
            or d['investment_score_certified'] is not False
            or type(d['factors']) is not dict):
            raise ValueError()
        diag = d['diagnostic']
        if (type(diag) is not dict or set(diag) != {'missing_pairs_examined',
                'restored_pairs', 'restored_with_price_quarantine',
                'restored_with_explicit_zero_amount', 'unavailable_pairs'}
            or any(type(v) is not int or not 0 <= v <= 20000 for v in diag.values())
            or diag['restored_pairs'] != len(d['factors'])
            or diag['missing_pairs_examined'] != diag['restored_pairs'] + diag['unavailable_pairs']
            or diag['restored_with_price_quarantine'] > len(d['factors'])
            or diag['restored_with_explicit_zero_amount'] > len(d['factors'])):
            raise ValueError()
        for key, factor in d['factors'].items():
            if (not re.fullmatch('(KOSPI|KOSDAQ):[0-9]{6}', key)
                or key in original['issuer_week_factors']
                or type(factor) is not dict
                or set(factor) != {'amount_100k_krw', 'observed_sessions'}
                or type(factor['amount_100k_krw']) is not int
                or not 0 <= factor['amount_100k_krw'] < 5 * 10**12
                or type(factor['observed_sessions']) is not int
                or factor['observed_sessions'] != len(original['session_dates'])):
                raise ValueError()
        return d
    except Exception:
        raise MoneyRepairError('invalid_money_repair_receipt') from None


def overlay(store, original_blob, original, state):
    """Load one index-pinned additive receipt; never mutate the original."""
    digest = state['weeks'].get(original['week_end'])
    if digest is None:
        return original, 0
    blob, _ = store.get_content(repair_path(original['week_end']))
    if blob is None or sha256(blob).hexdigest() != digest:
        raise MoneyRepairError('money_repair_receipt_digest_mismatch')
    extra = validate(blob, original_blob, original)['factors']
    return {**original, 'issuer_week_factors': {
        **original['issuer_week_factors'], **extra}}, len(extra)


def source_additions(client, original_blob, original, targets, budget):
    """Recover only explicit amounts; independently quarantine invalid prices."""
    values = {}
    quarantined = set()
    zeros = set()
    sessions = original['session_dates']
    for day in sessions:
        for market in ('KOSPI', 'KOSDAQ'):
            for attempt in range(3):
                if budget['requests'] >= MAX_CALLS:
                    raise MoneyRepairError('money_repair_request_budget_reached')
                budget['requests'] += 1
                try:
                    rows, raw = client.daily_market(market, day)
                    break
                except KRXProbeError as error:
                    if attempt == 2 or error.reason not in (
                            'empty_body', 'empty_market_rows', 'transport_failure', 'http_server_error'):
                        raise
                    time.sleep(attempt + 1)
            _snapshot_market(rows, raw, market=market, asof=day)
            for row in rows:
                key = market + ':' + row['ISU_CD']
                try:
                    amount = _numeric(row.get('ACC_TRDVAL'), 'ACC_TRDVAL')
                except ProbeError:
                    continue
                if amount >= 10**17:
                    continue
                values.setdefault(key, []).append(amount)
                if classify_row(row)[0] != 'valid_numeric_observation':
                    quarantined.add(key)
                if amount == 0:
                    zeros.add(key)
            del rows, raw
    totals = {key: {'amount_100k_krw': round(sum(v) / 100_000),
                    'observed_sessions': len(sessions)}
              for key, v in values.items() if len(v) == len(sessions)}
    # A changed established source is a separate revision investigation, never
    # permission to overwrite history or mix incompatible source vintages.
    if any(totals.get(key) != value for key, value in original['issuer_week_factors'].items()):
        raise MoneyRepairError('money_repair_established_source_changed')
    factors = {key: totals[key] for key in sorted(targets) if key in totals
               and key not in original['issuer_week_factors']}
    data = {'schema': SCHEMA, 'week_end': original['week_end'],
            'session_dates': sessions, 'original_sha256': sha256(original_blob).hexdigest(),
            'factors': factors, 'diagnostic': {
                'missing_pairs_examined': len(targets), 'restored_pairs': len(factors),
                'restored_with_price_quarantine': len(set(factors) & quarantined),
                'restored_with_explicit_zero_amount': len(set(factors) & zeros),
                'unavailable_pairs': len(targets) - len(factors)},
            'direct_reported_amount_only': True, 'price_or_direction_repaired': False,
            'investment_score_certified': False}
    blob = gzip.compress(canonical(data), compresslevel=9, mtime=0)
    validate(blob, original_blob, original)
    return data, blob


def load_history(store, catalog, *, day_loader=week_days):
    """Read the completed fixed weekly chain and its additive receipts."""
    store.verify_private()
    if store.repository != PRIVATE_REPO:
        raise MoneyRepairError('invalid_money_repair_repository')
    raw, _ = store.get_content(WEEKLY)
    state = manifest(raw, catalog['date'])
    if state['history_completed'] != HISTORY_WEEKS:
        raise MoneyRepairError('money_repair_week_history_incomplete')
    tail, _ = store.get_content(week_path(state['tail_week_end']))
    if tail is None or sha256(tail).hexdigest() != state['tail_sha256']:
        raise MoneyRepairError('money_repair_original_tail_changed')
    idx_raw, idx_sha = store.get_content(CHECKPOINT)
    idx = index(idx_raw)
    end = date.fromisoformat(state['anchor_week_end']) + timedelta(weeks=state['forward_completed'])
    history = []
    for i in range(HISTORY_WEEKS):
        week = (end - timedelta(weeks=i)).isoformat()
        blob, _ = store.get_content(week_path(week))
        original = validate_week(blob, week, day_loader(week))
        merged, _ = overlay(store, blob, original, idx)
        history.append((blob, original, merged))
    if store.get_content(WEEKLY)[0] != raw or store.get_content(CHECKPOINT) != (idx_raw, idx_sha):
        raise MoneyRepairError('money_repair_snapshot_moved')
    return history, idx_raw, idx_sha, idx


def completion_focus(priority, catalog):
    """Scheduling only: favor issuers missing just these two money components."""
    if priority.get('asof') != catalog.get('date'):
        return set()
    shapes = priority.get('shape_names', [])
    focus = set()
    if type(shapes) is not list:
        return focus
    members = {item.get('code') for item in catalog['rows']}
    for row in priority.get('multiple_missing_index', []):
        if (type(row) is not list or len(row) != 5 or row[0] not in members
            or type(row[3]) is not int or not 0 <= row[3] < len(shapes)
            or type(shapes[row[3]]) is not str):
            continue
        missing = set(shapes[row[3]].split('+'))
        if missing and missing <= {'volume:persistence', 'volume:weekly_structure_increment'}:
            focus.add(row[0])
    return focus


def select(history, catalog, examined, focus_codes=None):
    members = set()
    for item in catalog['rows']:
        code, market = item.get('code'), item.get('market')
        if (type(code) is not str or not re.fullmatch('[0-9]{6}', code)
            or market not in ('KOSPI', 'KOSDAQ') or code in members):
            raise MoneyRepairError('invalid_money_repair_catalog')
        members.add(code)
    if len(members) != catalog['listed_total']:
        raise MoneyRepairError('incomplete_money_repair_catalog')
    targets, single, focused_single, focused_share = {}, {}, {}, {}
    internal_issuers = 0
    for item in catalog['rows']:
        key = item['market'] + ':' + item['code']
        present = [key in h[2]['issuer_week_factors'] for h in history]
        positions = [i for i, yes in enumerate(present) if yes]
        if not positions:
            continue
        gaps = [i for i in range(min(positions), max(positions) + 1) if not present[i]]
        if gaps:
            internal_issuers += 1
        for i in gaps:
            week = history[i][1]['week_end']
            if week in examined:
                continue
            targets.setdefault(week, set()).add(key)
            # This closes weekly evidence only if there are no other holes,
            # including at the latest or oldest edge of the inspected chain.
            if sum(not yes for yes in present) == 1:
                single[week] = single.get(week, 0) + 1
            if item['code'] in (focus_codes or set()):
                focused_share[week] = focused_share.get(week, 0) + 1 / sum(not yes for yes in present)
                if sum(not yes for yes in present) == 1:
                    focused_single[week] = focused_single.get(week, 0) + 1
    order = sorted(targets, key=lambda w: (-focused_single.get(w, 0),
        -focused_share.get(w, 0), -single.get(w, 0), -len(targets[w]), w))
    return order, targets, single, internal_issuers


def save_immutable_receipt(writer, name, blob):
    """Retry branch-head contention using the same bytes, never the provider."""
    for attempt in range(4):
        try:
            writer.put_new(name, blob, 'Save additive reported-money week repair proof')
            break
        except PrivateHistoryError as error:
            if getattr(error, 'safe_http_status', None) != 'http_409':
                raise
            observed, _ = writer.get_content(name)
            if observed is not None:
                if observed != blob:
                    raise MoneyRepairError('money_repair_immutable_receipt_conflict') from None
                break
            if attempt == 3:
                raise
            time.sleep(.5 * (attempt + 1))
    _private_new_blob_readback(writer, name, blob)


def save_index(writer, encoded, raw, oldsha):
    for attempt in range(3):
        try:
            return _save_cursor(writer, encoded, raw, oldsha, checkpoint=CHECKPOINT)
        except PrivateHistoryError as error:
            if getattr(error, 'safe_http_status', None) != 'http_409':
                raise
            observed, observed_sha = writer.get_content(CHECKPOINT)
            if observed == encoded:
                return observed_sha
            if observed != raw or observed_sha != oldsha:
                raise MoneyRepairError('money_repair_index_concurrent_change') from None
            if attempt == 2:
                raise
            time.sleep(.5 * (attempt + 1))


def repair(snapshot, writer, client, catalog, *, max_weeks=MAX_WEEKS,
           day_loader=week_days, focus_codes=None):
    if type(max_weeks) is not int or not 1 <= max_weeks <= MAX_WEEKS:
        raise MoneyRepairError('invalid_money_repair_week_budget')
    history, raw, oldsha, state = load_history(snapshot, catalog, day_loader=day_loader)
    writer.verify_private()
    writer_raw, writer_sha = writer.get_content(CHECKPOINT)
    if writer.repository != PRIVATE_REPO or writer_raw != raw:
        raise MoneyRepairError('money_repair_writer_snapshot_mismatch')
    # Snapshot hashes are SHA256 integrity digests. GitHub CAS requires the
    # remote Git blob SHA; never substitute the local snapshot digest.
    oldsha = writer_sha
    order, targets, single, internal = select(history, catalog, state['weeks'], focus_codes)
    lookup = {h[1]['week_end']: h[:2] for h in history}
    budget = {'requests': 0}
    summaries = []
    for week in order[:max_weeks]:
        original_blob, original = lookup[week]
        if writer.get_content(week_path(week))[0] != original_blob:
            raise MoneyRepairError('money_repair_writer_original_mismatch')
        blob, _ = writer.get_content(repair_path(week))
        if blob is None:
            print(json.dumps({'checking_gap_week':week,
                'missing_pairs':len(targets[week]),
                'single_week_gap_candidates':single.get(week,0)}),flush=True)
            data, blob = source_additions(client, original_blob, original, targets[week], budget)
            save_immutable_receipt(writer, repair_path(week), blob)
        else:
            data = validate(blob, original_blob, original)
        updated = {'schema': INDEX_SCHEMA, 'weeks': {
            **state['weeks'], week: sha256(blob).hexdigest()}}
        encoded = canonical(updated)
        index(encoded)
        oldsha = save_index(writer, encoded, raw, oldsha)
        raw, state = encoded, updated
        summary = {'week_end': week, **data['diagnostic'],
                   'single_week_gap_candidates': single.get(week, 0)}
        summaries.append(summary)
        print(json.dumps({'checkpointed_week_repair': summary}), flush=True)
    return {'internal_gap_issuers_before': internal, 'weeks_examined': len(summaries),
            'remaining_unexamined_gap_weeks': max(0, len(order) - len(summaries)),
            'source_requests': budget['requests'],
            'restored_issuer_weeks': sum(s['restored_pairs'] for s in summaries),
            'original_archives_overwritten': False, 'prices_or_directions_repaired': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private-snapshot', required=True)
    p.add_argument('--max-weeks', type=int, default=3, choices=range(1, MAX_WEEKS + 1))
    args = p.parse_args()
    from v3_actual_money_participation import LocalPrivateSnapshot
    catalog = json.loads(Path('docs/v22-market-catalog.json').read_text())
    priority = json.loads(Path('docs/v3-operational-evidence-priority.json').read_text())
    token = os.environ['V3_PRIVATE_ARCHIVE_TOKEN']
    if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
        raise MoneyRepairError('invalid_money_repair_repository')
    result = repair(LocalPrivateSnapshot(args.private_snapshot, token),
                    PrivateGitStorage(PRIVATE_REPO, token),
                    KRXClient(os.environ['KRX_API_KEY']), catalog, max_weeks=args.max_weeks,
                    focus_codes=completion_focus(priority, catalog))
    print(json.dumps(result, sort_keys=True))


def failure_diagnostic(error):
    if isinstance(error, KRXProbeError):
        return {'kind':'official_krx_source',
                'reason':error.reason if error.reason in KRXProbeError.REASONS else 'other_source_failure',
                'http_status':error.http_status}
    if isinstance(error, PrivateHistoryError):
        return {'kind':'private_storage','details':storage_diagnostic(error)}
    if isinstance(error, MoneyRepairError):
        return {'kind':'repair_contract','reason':str(error)}
    return {'kind':'source_or_snapshot_contract'}


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Provider/transport exceptions may contain URLs or source identifiers.
        print(json.dumps({'status': 'blocked', 'diagnostic':failure_diagnostic(error),
                          'existing_archives_preserved': True, 'source_payload_logged': False}))
        raise SystemExit(1)
