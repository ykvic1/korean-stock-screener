"""Publish aggregate research outcomes for a matured forward price window."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
from statistics import median

from v3_forward_cash_dividends import load_dividends
from v3_forward_daily_prices import completed_sessions, read_daily
from v3_forward_entry import PRIVATE_REPO, ROW_STATES, entry_plan, entry_stop_state, public_result
from v3_forward_horizon_readiness import HORIZONS
from v3_forward_liquidations import OBSERVED as LIQUIDATION_OBSERVED, load_liquidations
from v3_forward_observed_readiness import build, read_archived_days
from v3_forward_price_replay import replay_price_path
from v3_forward_rank_comparison import add_rank_comparisons
from v3_forward_settlements import load_settlements
from v3_forward_share_actions import load_forward_actions, normalized_name
from v3_forward_signal_close import compare_closes, frozen_market
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_regular_close import KST
from v3_score_observation_capture import ARCHIVE

OUT = Path('ops/forward-price-outcomes')
SCHEMA = 'v3-forward-horizon-price-outcome-7'
READY = 'daily_archive_ready_for_path_replay'


def loss_rate_bounds(negative, unvalued, opened):
    """Conservative percentage bounds, rounded outwards without imputation."""
    if not opened:
        return None
    decimals = 10_000
    lower = (negative * 100 * decimals) // opened
    upper, remainder = divmod((negative + unvalued) * 100 * decimals, opened)
    return [lower / decimals, (upper + bool(remainder)) / decimals]


def aggregate_paths(observation, openings, sessions, panels, notices, *, signal=None, cases=None,
                    dividends=None, settlements=None):
    members = observation['rows']
    entry_rows = panels[sessions[0]]['rows']
    outcomes = {}
    dividends = dividends or {}
    for code, member in members.items():
        if member[3] is None:
            continue
        state, opening = openings[code]
        if state != 'official_open_observed':
            outcomes[code] = {'status': state}
            continue
        row = entry_rows.get(code)
        if (row is None or row[0] != member[1]
                or normalized_name(row[1]) != normalized_name(member[0])
                or row[2] != 'traded' or row[3] != opening):
            raise ValueError('horizon_entry_open_crosscheck_failed')
        reference = None
        if signal is not None and (sessions[0], code) not in notices:
            state = entry_stop_state(member, opening, row, signal['rows'].get(code),
                                     cases[code]['state'])
            if state in ('open_at_or_below_reference', 'intraday_reference_touched',
                         'reference_not_touched'):
                reference = member[7]
        bars = {day: panel['rows'].get(code) for day, panel in panels.items()}
        outcomes[code] = replay_price_path(code, sessions, bars, entry_open=opening,
                                           notices=notices, reference=reference,
                                           dividends=dividends.get(code, ()),
                                           settlements=settlements, panels=panels)
    result = {}
    for label, limit in (('top20', 20), ('top100', 100), ('all_ranked', None)):
        codes = [code for code in outcomes if limit is None or members[code][3] <= limit]
        selected = [outcomes[code] for code in codes]
        entries = Counter(openings[code][0] for code in codes)
        opened = entries['official_open_observed']
        observed = [row for row in selected if row['status'] == 'observed_price_path']
        settled = [row for row in selected if row['status'] == 'observed_delisting_settlement']
        liquidation = [row for row in selected if row['status'] == LIQUIDATION_OBSERVED]
        valued = observed + settled
        unvalued = opened - len(valued)
        losses = sum(row['terminal_return_pct'] < 0 for row in valued)
        missing = Counter(row['status'] for row in selected if row['status'] not in
                          ('observed_price_path', 'observed_delisting_settlement', LIQUIDATION_OBSERVED))
        paid = [row for row in observed if row['confirmed_dividend_events']]
        result[label] = {
            'selected_count': len(selected),
            'entry_status_counts': {state: entries[state] for state in ROW_STATES if state != 'unranked'},
            'observed_price_path_count': len(observed),
            'unresolved_counts': dict(sorted(missing.items())),
            # Confirmed openings remain in the denominator even if the end value
            # is unresolved or available only as a liquidation close scenario.
            'terminal_return_coverage': {
                'valued_count': len(valued),
                'unvalued_confirmed_openings_count': unvalued,
                'negative_return_count': losses,
                'loss_rate_bounds_pct': loss_rate_bounds(losses, unvalued, opened),
            },
            'median_terminal_return_pct': metric_median(observed, 'terminal_return_pct'),
            'median_mae_pct': metric_median(observed, 'mae_pct'),
            'median_mfe_pct': metric_median(observed, 'mfe_pct'),
            'reference_compared_count': sum(row['reference_compared'] for row in observed),
            'reference_touched_count': sum(row['first_reference_touch_session'] is not None
                                           for row in observed),
            'delisting_settlements': {
                'observed_count': len(settled),
                'counts_by_treatment': dict(sorted(Counter(row['treatment'] for row in settled).items())),
                'median_settlement_terminal_return_pct': metric_median(settled, 'terminal_return_pct'),
                'median_terminal_return_including_settlements_pct': metric_median(
                    valued, 'terminal_return_pct'),
            },
            'liquidation_exit_proxies': {
                'observed_count': len(liquidation),
                'median_final_session_close_proxy_return_pct': metric_median(liquidation, 'terminal_return_pct'),
                'actual_execution_validated': False,
            },
            'cash_dividends': {
                'issuers_with_confirmed_payment': len(paid),
                'confirmed_payment_events': sum(row['confirmed_dividend_events'] for row in observed),
                'entitled_events_awaiting_payment_evidence': sum(row['pending_dividend_events'] for row in observed),
                'events_with_unlinked_issuer_identity': sum(row['unlinked_dividend_events'] for row in observed),
                'median_price_plus_confirmed_dividend_pct': metric_median(paid, 'price_plus_confirmed_dividend_pct'),
            },
        }
    comparable = {code: row['terminal_return_pct'] for code, row in outcomes.items()
                  if row['status'] in ('observed_price_path', 'observed_delisting_settlement')}
    add_rank_comparisons(result, members, openings, comparable)
    return result


def metric_median(rows, key):
    return round(median(row[key] for row in rows), 4) if rows else None


def replay_next(root, now, get_storage):
    """Replay one oldest changed window per run, with no archive access while waiting."""
    archived = read_archived_days(root)
    readiness = build(root, now)
    notices = load_forward_actions(root)
    evidence_asof = now.astimezone(KST).date().isoformat()
    dividends = load_dividends(root, evidence_asof)
    settlements = load_settlements(root, evidence_asof)
    liquidations = load_liquidations(root, evidence_asof)
    for score_day, cohort in readiness['cohorts'].items():
        for months in HORIZONS:
            horizon = cohort['horizons'][str(months)]
            if horizon['status'] != READY:
                continue
            entry_day = cohort['entry_session_candidate']
            target = horizon['target_session_candidate']
            days = list(completed_sessions(date.fromisoformat(entry_day),
                                           date.fromisoformat(target), now))
            if (not days or days[0] != entry_day or days[-1] != target
                    or len(days) != horizon['archived_session_count']):
                raise ValueError('horizon_session_window_mismatch')
            # A reference is comparable only on a directly adjacent signal
            # basis. Longer pre-entry gaps need their own share-path replay.
            reference_day = (score_day if score_day in archived and
                cohort['entry_share_action_review']['previous_session_candidate'] == score_day
                else None)
            sources = {day: archived[day] for day in days}
            if reference_day:
                sources[reference_day] = archived[reference_day]
            applicable_notices = {key: value for key, value in notices.items() if key[0] in days}
            applicable_settlements = {key: value for key, value in settlements.items()
                                      if entry_day < key[0] <= target}
            applicable_liquidations = {(max(entry_day, row['start_on']), code): row
                                       for code, row in liquidations.items()
                                       if entry_day <= row['last_on'] and row['start_on'] <= target}
            if ({key[1] for key in applicable_settlements}
                    & {key[1] for key in applicable_liquidations}):
                raise ValueError('conflicting_forward_delisting_treatments')
            applicable_settlements.update(applicable_liquidations)
            dividend_events = [dividends[k] for k in sorted(dividends)
                               if len(days) >= 3 and days[2] <= k[1] <= target
                               and dividends[k]['terms_report_on'] <= target]
            grouped_dividends = {}
            for event in dividend_events:
                grouped_dividends.setdefault(event['code'], []).append(event)
            manifest = [SCHEMA, months, cohort['score_observation_sha256'], days,
                        reference_day, sources, [applicable_notices[k] for k in sorted(applicable_notices)],
                        dividend_events, [applicable_settlements[k] for k in sorted(applicable_settlements)]]
            digest = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
            target_path = root / OUT / score_day / f'{months}m.json'
            if target_path.exists() and json.loads(target_path.read_bytes()).get('inputs_sha256') == digest:
                continue
            raw = (root / ARCHIVE / f'{score_day}.json').read_bytes()
            if sha256(raw).hexdigest() != cohort['score_observation_sha256']:
                raise ValueError('horizon_frozen_cohort_changed')
            observation = json.loads(raw)
            plan = entry_plan(observation, cohort['score_observation_sha256'])
            storage = get_storage()
            blob, _ = storage.get_content(f'derived/forward-entry/{score_day}.json.gz')
            if blob is None:
                raise ValueError('horizon_private_openings_missing')
            evidence = json.loads(gzip.decompress(blob))
            entry = public_result(plan, evidence, observation['rows'])
            if (entry['krx_market_source_sha256'] != sources[entry_day]
                    or entry['entry_status_counts']['official_open_observed'] != cohort['matched_opening_count']):
                raise ValueError('horizon_opening_archive_source_mismatch')

            def panel(day):
                daily = read_daily(day, storage)
                if daily is None or daily['source_sha256'] != sources[day]:
                    raise ValueError('horizon_daily_archive_source_mismatch')
                return daily

            with ThreadPoolExecutor(max_workers=4) as pool:
                panels = dict(zip(days, pool.map(panel, days)))
            signal = panel(reference_day) if reference_day else None
            cases = None
            if signal is not None:
                market = frozen_market(observation)
                if market is None:
                    raise ValueError('horizon_frozen_market_unavailable')
                _, cases = compare_closes(observation, market, signal)
            report = {
                'schema': SCHEMA, 'score_asof': score_day, 'months': months,
                'entry_session': entry_day, 'target_date': horizon['target_date'],
                'evaluation_session_candidate': target, 'session_count': len(days),
                'inputs_sha256': digest,
                'groups': aggregate_paths(observation, evidence['rows'], days, panels,
                                          applicable_notices, signal=signal, cases=cases,
                                          dividends=grouped_dividends,
                                          settlements=applicable_settlements),
                'cash_distributions_and_costs_included': False,
                'dividend_coverage_complete': False,
                'delisting_settlement_coverage_complete': False,
                'liquidation_window_coverage_complete': False,
                'full_corporate_action_history_certified': False,
                'performance_validated': False,
            }
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            return {'status': 'price_path_aggregated', 'score_asof': score_day, 'months': months}
    return {'status': 'no_new_ready_horizons'}


def private_storage():
    if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
        raise ValueError('horizon_private_archive_not_configured')
    storage = PrivateGitStorage(PRIVATE_REPO, os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', ''))
    storage.verify_private()
    return storage


if __name__ == '__main__':
    print(json.dumps(replay_next(Path('.'), datetime.now(timezone.utc), private_storage)))
