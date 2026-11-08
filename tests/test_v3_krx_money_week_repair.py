import copy
from datetime import date, timedelta
from hashlib import sha256
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_v3_krx_calendar_week_money as weeks
from test_v3_krx_money_backfill import FakePrivateStore
import v3_krx_money_week_repair as repair
from v3_krx_calendar_week_money import derive, canonical, CHECKPOINT, path


class Client:
    def __init__(self, amount='0', bad_price=True):
        self.calls = []
        self.amount = amount
        self.bad_price = bad_price
        self.changed = False
        self.wrong_date = False

    def daily_market(self, market, day):
        self.calls.append((market, day))
        rows, _ = weeks.Client().daily_market(market, day)
        if self.changed:
            rows[0]['ACC_TRDVAL'] = '999999999'
        extra = {**rows[0], 'ISU_CD': '000002'}
        if self.bad_price:
            extra['TDD_OPNPRC'] = '0'
        if self.amount is None:
            extra.pop('ACC_TRDVAL')
        else:
            extra['ACC_TRDVAL'] = self.amount
        if self.wrong_date:
            extra['BAS_DD'] = '20200101'
        rows.append(extra)
        return rows, json.dumps({'OutBlock_1': rows}).encode()


def original(week='2026-11-01', missing=True):
    data, _ = derive(weeks.Client(), week, weeks.days(week), {'requests': 0})
    if not missing:
        for market in ('KOSPI', 'KOSDAQ'):
            data['issuer_week_factors'][market + ':000002'] = {
                'amount_100k_krw': 0, 'observed_sessions': 5}
    blob = gzip.compress(canonical(data), mtime=0)
    return blob, data


def setup_store():
    store = FakePrivateStore()
    ends = ['2026-11-01', '2026-10-25', '2026-10-18']
    for i, week in enumerate(ends):
        store.files[path(week)] = original(week, missing=i == 1)[0]
    state = {'schema': 'v3-private-calendar-week-actual-money-cursor-v1',
             'anchor_week_end': ends[0], 'history_completed': 3, 'forward_completed': 0,
             'compressed_bytes': sum(len(b) for b in store.files.values()),
             'tail_week_end': ends[-1],
             'tail_sha256': sha256(store.files[path(ends[-1])]).hexdigest()}
    # Use the production cursor schema rather than a duplicated literal.
    from v3_krx_calendar_week_money import CURSOR_SCHEMA
    state['schema'] = CURSOR_SCHEMA
    store.files[CHECKPOINT] = canonical(state)
    catalog = {'date': '2026-11-04', 'listed_total': 2,
               'rows': [{'code': '000001', 'market': 'KOSPI'},
                        {'code': '000002', 'market': 'KOSPI'}]}
    return store, catalog


class RepairTests(unittest.TestCase):
    def test_explicit_zero_money_is_observed_even_when_price_is_quarantined(self):
        blob, data = original()
        client = Client()
        result, receipt = repair.source_additions(client, blob, data,
            {'KOSPI:000002'}, {'requests': 0})
        self.assertEqual(result['factors']['KOSPI:000002'], {
            'amount_100k_krw': 0, 'observed_sessions': 5})
        self.assertEqual(result['diagnostic']['restored_with_price_quarantine'], 1)
        self.assertEqual(result['diagnostic']['restored_with_explicit_zero_amount'], 1)
        self.assertEqual(len(client.calls), 10)
        text = gzip.decompress(receipt).decode()
        for field in ('ACC_TRDVAL', 'TDD_OPNPRC', 'TDD_CLSPRC', '123456789'):
            self.assertNotIn(field, text)
        self.assertFalse(result['price_or_direction_repaired'])

    def test_missing_amount_is_never_zero_and_nonzero_amount_needs_no_price_sign(self):
        blob, data = original()
        for amount, expected in [(None, 0), ('-', 0), ('-1', 0), ('NaN', 0), ('17.5', 0),
                                 ('999999999999999999', 0), ('100,000', 1)]:
            result, _ = repair.source_additions(Client(amount=amount), blob, data,
                {'KOSPI:000002'}, {'requests': 0})
            self.assertEqual(len(result['factors']), expected)
            if expected:
                self.assertEqual(result['factors']['KOSPI:000002']['amount_100k_krw'], 5)

    def test_changed_established_data_and_wrong_session_fail_closed(self):
        blob, data = original()
        changed = Client(); changed.changed = True
        with self.assertRaisesRegex(repair.MoneyRepairError, 'established_source_changed'):
            repair.source_additions(changed, blob, data, {'KOSPI:000002'}, {'requests': 0})
        wrong = Client(); wrong.wrong_date = True
        with self.assertRaises(ValueError):
            repair.source_additions(wrong, blob, data, {'KOSPI:000002'}, {'requests': 0})
        with self.assertRaisesRegex(repair.MoneyRepairError, 'budget_reached'):
            repair.source_additions(Client(), blob, data, {'KOSPI:000002'}, {'requests': repair.MAX_CALLS})

    def test_additive_receipt_is_digest_pinned_and_cannot_replace_original(self):
        blob, data = original()
        _, receipt = repair.source_additions(Client(), blob, data, {'KOSPI:000002'}, {'requests': 0})
        store = FakePrivateStore()
        store.files[repair.repair_path(data['week_end'])] = receipt
        idx = {'schema': repair.INDEX_SCHEMA, 'weeks': {data['week_end']: sha256(receipt).hexdigest()}}
        merged, count = repair.overlay(store, blob, data, idx)
        self.assertEqual(count, 1)
        self.assertNotIn('KOSPI:000002', data['issuer_week_factors'])
        self.assertIn('KOSPI:000002', merged['issuer_week_factors'])
        for field, value in [('investment_score_certified', True),
                             ('original_sha256', 'a' * 64),
                             ('session_dates', ['2026-01-01'])]:
            tampered = json.loads(gzip.decompress(receipt)); tampered[field] = value
            with self.assertRaises(repair.MoneyRepairError):
                repair.validate(gzip.compress(canonical(tampered)), blob, data)
        tampered = json.loads(gzip.decompress(receipt))
        tampered['factors'] = {'KOSPI:000001': next(iter(tampered['factors'].values()))}
        with self.assertRaises(repair.MoneyRepairError):
            repair.validate(gzip.compress(canonical(tampered)), blob, data)
        store.files[repair.repair_path(data['week_end'])] += b'x'
        with self.assertRaisesRegex(repair.MoneyRepairError, 'digest_mismatch'):
            repair.overlay(store, blob, data, idx)

    def test_internal_holes_are_repaired_once_and_original_archive_is_immutable(self):
        store, catalog = setup_store()
        originals = copy.deepcopy(store.files)
        client = Client()
        with patch.object(repair, 'HISTORY_WEEKS', 3):
            result = repair.repair(store, store, client, catalog, day_loader=weeks.days)
            self.assertEqual(result['restored_issuer_weeks'], 1)
            self.assertEqual(result['source_requests'], 10)
            for name, content in originals.items():
                self.assertEqual(store.files[name], content)
            repeated = repair.repair(store, store, client, catalog, day_loader=weeks.days)
            self.assertEqual(repeated['source_requests'], 0)
            self.assertEqual(repeated['internal_gap_issuers_before'], 0)
        self.assertEqual(len(client.calls), 10)

    def test_unavailable_week_is_checkpointed_and_not_blindly_refetched(self):
        store, catalog = setup_store()
        with patch.object(repair, 'HISTORY_WEEKS', 3):
            result = repair.repair(store, store, Client(amount=None), catalog, day_loader=weeks.days)
            self.assertEqual(result['restored_issuer_weeks'], 0)
            again = repair.repair(store, store, Client(), catalog, day_loader=weeks.days)
            self.assertEqual(again['source_requests'], 0)
            self.assertEqual(again['internal_gap_issuers_before'], 1)

    def test_orphan_receipt_resumes_without_new_official_calls(self):
        store, catalog = setup_store()
        week = '2026-10-25'
        blob, data = original(week)
        _, receipt = repair.source_additions(Client(), blob, data, {'KOSPI:000002'}, {'requests': 0})
        store.files[repair.repair_path(week)] = receipt
        with patch.object(repair, 'HISTORY_WEEKS', 3):
            result = repair.repair(store, store, Client(), catalog, day_loader=weeks.days)
            self.assertEqual(result['source_requests'], 0)
            self.assertEqual(result['restored_issuer_weeks'], 1)

    def test_cursor_cas_uses_remote_git_blob_sha_not_snapshot_integrity_digest(self):
        store, catalog = setup_store()
        store.files[repair.CHECKPOINT] = canonical({'schema': repair.INDEX_SCHEMA,
            'weeks': {'2020-01-05': 'a' * 64}})
        class Writer(FakePrivateStore):
            def get_content(self, name):
                raw, sha = super().get_content(name)
                return raw, 'remote-git-blob-sha' if name == repair.CHECKPOINT else sha
        writer = Writer(); writer.files = store.files
        with patch.object(repair, 'HISTORY_WEEKS', 3), patch.object(
                repair, '_save_cursor', return_value='next-remote-sha') as save:
            repair.repair(store, writer, Client(), catalog, max_weeks=1, day_loader=weeks.days)
        self.assertEqual(save.call_args.args[3], 'remote-git-blob-sha')

    def test_prioritizes_single_gap_without_calling_tail_missing_a_listing(self):
        history = []
        for i in range(5):
            factors = {}
            for code, present in [('000001', i != 2), ('000002', i < 3),
                                  ('000003', i not in (1, 3))]:
                if present: factors['KOSPI:' + code] = {}
            week = (date(2026, 11, 1) - timedelta(weeks=i)).isoformat()
            row = {'week_end': week, 'issuer_week_factors': factors}
            history.append((b'', row, row))
        catalog = {'rows': [{'code': f'{i:06d}', 'market': 'KOSPI'} for i in range(1, 4)],
                   'listed_total': 3}
        order, targets, singles, count = repair.select(history, catalog, {})
        self.assertEqual(order[0], '2026-10-18')
        self.assertEqual(singles[order[0]], 1)
        self.assertEqual(count, 2)
        self.assertFalse(any('KOSPI:000002' in t for t in targets.values()))
        focused, _, _, _ = repair.select(history, catalog, {}, {'000003'})
        self.assertIn(focused[0], ('2026-10-11', '2026-10-25'))

    def test_focus_uses_missing_component_evidence_and_never_changes_scoring(self):
        catalog = {'date': '2026-11-06', 'rows': [{'code': '000001'}, {'code': '000002'}]}
        priority = {'asof': catalog['date'], 'shape_names': [
            'volume:persistence+volume:weekly_structure_increment',
            'financial:cashflow_fcf_persistence+volume:persistence'],
            'multiple_missing_index': [['000001', 97.5, 2.5, 0, 'priced'],
                                       ['000002', 90, 10, 1, 'priced']]}
        self.assertEqual(repair.completion_focus(priority, catalog), {'000001'})
        priority['asof'] = '2026-11-05'
        self.assertEqual(repair.completion_focus(priority, catalog), set())

    def test_workflow_runs_no_secret_on_pr_and_no_raw_artifact_upload(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/v3-krx-money-week-repair.yml').read_text()
        offline, live = text.split('  repair:', 1)
        self.assertNotIn('secrets.', offline)
        self.assertIn("if: github.event_name != 'pull_request'", live)
        self.assertIn('v3-krx-actual-money-private-derived-backfill', live)
        self.assertNotIn('upload-artifact', text)
        self.assertNotIn('git add', text)

    def test_private_repair_replays_into_full_long_window_without_exporting_amounts(self):
        import v3_actual_money_participation as money
        from test_v3_actual_money_participation import fixture, week_days
        store, days, forward, catalog = fixture()
        week = (date.fromisoformat(money.closed_week_end(catalog['date'])) - timedelta(weeks=150)).isoformat()
        name = money.week_path(week)
        original_data = json.loads(gzip.decompress(store.files[name]))
        original_data['issuer_week_factors'].pop('KOSPI:000001')
        original_blob = gzip.compress(canonical(original_data))
        store.files[name] = original_blob
        options = dict(asof=catalog['date'], catalog=catalog, source_revision='a' * 40,
                       days=days, digest='a' * 64, forward_days=forward, week_loader=week_days)
        before = money.collect(store, **options)
        self.assertEqual(before['eligible_issuers'], 0)
        _, receipt = repair.source_additions(weeks.Client(), original_blob, original_data,
                                            {'KOSPI:000001'}, {'requests': 0})
        store.files[repair.repair_path(week)] = receipt
        store.files[repair.CHECKPOINT] = canonical({'schema': repair.INDEX_SCHEMA,
            'weeks': {week: sha256(receipt).hexdigest()}})
        after = money.collect(store, **options)
        self.assertEqual(after['eligible_issuers'], 1)
        self.assertEqual(after['component_records']['000001']['component_observations']
                         ['components']['persistence'][1], 5)
        self.assertIsNotNone(after['component_records']['000001']['component_observations']
                             ['components']['weekly_structure_increment'])
        self.assertEqual(after['weekly_repair_observation']['added_issuer_weeks'], 1)
        self.assertEqual(store.files[name], original_blob)
        for field in ('amount_100k_krw', 'ACC_TRDVAL', 'TDD_CLSPRC', '123456789'):
            self.assertNotIn(field, json.dumps(after))

    def test_future_week_and_five_session_collectors_keep_money_without_price_sign(self):
        from v3_krx_money_5day import derive as derive_five
        week = '2026-11-01'
        client = Client()
        observed, _ = derive(client, week, weeks.days(week), {'requests': 0})
        self.assertEqual(observed['issuer_week_factors']['KOSPI:000002'], {
            'amount_100k_krw': 0, 'observed_sessions': 5})
        data, _, _ = derive_five(Client(amount='100000'), weeks.days(week))
        factor = data['derived_issuer_money_factors']['KOSPI:000002']
        self.assertEqual(factor['total_actual_amount_100k_krw'], 5)
        self.assertIsNone(factor['money_pressure_intrabucket_bp'])
        self.assertIsNone(factor['intrabucket_price_change_bp'])
        missing, _, _ = derive_five(Client(amount=None), weeks.days(week))
        self.assertNotIn('KOSPI:000002', missing['derived_issuer_money_factors'])

    def test_transient_source_retry_is_bounded_and_diagnostic_contains_no_payload(self):
        from official_probe.clients import KRXProbeError
        blob, data = original()
        client = Client()
        original_call = client.daily_market
        count = [0]
        def flaky(market, day):
            count[0] += 1
            if count[0] == 1:
                raise KRXProbeError(market, 'empty_market_rows', 200)
            return original_call(market, day)
        budget = {'requests': 0}
        with patch.object(client, 'daily_market', side_effect=flaky), patch.object(repair.time, 'sleep'):
            result, _ = repair.source_additions(client, blob, data, {'KOSPI:000002'}, budget)
        self.assertEqual(budget['requests'], 11)
        self.assertEqual(result['diagnostic']['restored_pairs'], 1)
        with patch.object(client, 'daily_market', side_effect=KRXProbeError(
                'KOSPI', 'empty_market_rows', 200)) as call, patch.object(repair.time, 'sleep'):
            with self.assertRaises(KRXProbeError) as caught:
                repair.source_additions(client, blob, data, {'KOSPI:000002'}, {'requests': 0})
        self.assertEqual(call.call_count, 3)
        self.assertEqual(repair.failure_diagnostic(caught.exception), {
            'kind': 'official_krx_source', 'reason': 'empty_market_rows', 'http_status': 200})

    def test_repository_branch_contention_retries_saved_bytes_without_refetching_krx(self):
        from test_v3_krx_money_backfill import private_409
        class ContendedStore(FakePrivateStore):
            receipt_attempts = 0
            index_attempts = 0
            def put_new(self, name, payload, message):
                if name.startswith(repair.PREFIX):
                    self.receipt_attempts += 1
                    if self.receipt_attempts <= 2:
                        raise private_409()
                if name == repair.CHECKPOINT:
                    self.index_attempts += 1
                    if self.index_attempts <= 3:
                        raise private_409()
                return super().put_new(name, payload, message)
        base, catalog = setup_store()
        store = ContendedStore(); store.files = base.files
        client = Client()
        with patch.object(repair, 'HISTORY_WEEKS', 3), patch.object(repair.time, 'sleep'):
            result = repair.repair(store, store, client, catalog, day_loader=weeks.days)
        self.assertEqual(result['restored_issuer_weeks'], 1)
        self.assertEqual(len(client.calls), 10)
        self.assertEqual(store.receipt_attempts, 3)
        self.assertEqual(store.index_attempts, 4)

    def test_conflicting_receipt_is_never_overwritten_and_partial_failure_is_replayed(self):
        from test_v3_krx_money_backfill import private_409
        store = FakePrivateStore()
        name = repair.repair_path('2026-11-01')
        store.files[name] = b'existing-immutable-data'
        with patch.object(store, 'put_new', side_effect=private_409()), patch.object(repair.time, 'sleep'):
            with self.assertRaisesRegex(repair.MoneyRepairError, 'immutable_receipt_conflict'):
                repair.save_immutable_receipt(store, name, b'different-data')
        self.assertEqual(store.files[name], b'existing-immutable-data')
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/v3-actual-money-score.yml').read_text()
        self.assertIn("github.event.workflow_run.name == 'V3 Targeted Reported Money Week Repair'", text)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", text)
        self.assertIn('github.event.workflow_run.head_repository.full_name == github.repository', text)


if __name__ == '__main__':
    unittest.main()
