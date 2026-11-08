import copy
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_v3_krx_money_week_repair as weekly_tests
from test_v3_krx_money_backfill import FakePrivateStore, private_409
from test_v3_actual_money_participation import fixture, week_days
import v3_actual_money_participation as money
import v3_krx_money_daily_repair as daily
from v3_krx_money_5day import derive, validate_money_blob
from v3_krx_calendar_week_money import canonical


class Client(weekly_tests.Client):
    def daily_market(self, market, day):
        rows, _ = super().daily_market(market, day)
        rows[0]['ISU_CD'], rows[1]['ISU_CD'] = '000002', '000001'
        return rows, json.dumps({'OutBlock_1': rows}).encode()


def original():
    sessions = weekly_tests.weeks.days('2026-11-01')
    data, blob, _ = derive(Client(amount=None), sessions)
    return sessions, data, blob


def missing_fixture():
    store, days, forward, catalog = fixture()
    windows, originals, _ = money.read_daily_history(store, asof=catalog['date'],
        before={p:store.get_content(p) for p in (money.CHECKPOINT,money.FORWARD)},
        days=days,digest='a'*64,forward_days=forward)
    sessions = windows[20]
    data = copy.deepcopy(originals[20])
    data['derived_issuer_money_factors'].pop('KOSPI:000001')
    data['complete_five_session_issuer_market_pairs'] = 0
    store.files[money._path(sessions)] = gzip.compress(canonical(data))
    return store, days, forward, catalog, sessions


class DailyRepairTests(unittest.TestCase):
    def test_explicit_money_can_be_recovered_without_a_price_or_direction(self):
        sessions, data, blob = original()
        client = Client(amount='0')
        receipt, raw = daily.source_additions(client, blob, data, sessions,
            {'KOSPI:000001'}, {'requests':0})
        self.assertEqual(len(client.calls),10)
        self.assertEqual(receipt['amount_factors']['KOSPI:000001'],
            {'total_actual_amount_100k_krw':0,'positive_amount_days':0})
        self.assertEqual(receipt['diagnostic']['restored_with_unavailable_price_change'],1)
        self.assertEqual(receipt['diagnostic']['restored_with_explicit_zero_amount'],1)
        store = FakePrivateStore()
        store.files[daily.repair_path(receipt['period'])] = raw
        idx = {'schema':daily.INDEX_SCHEMA,'windows':{receipt['period']:sha256(raw).hexdigest()}}
        updated, count = daily.overlay(store,blob,data,sessions,idx)
        self.assertEqual(count,1)
        factor = updated['derived_issuer_money_factors']['KOSPI:000001']
        self.assertIsNone(factor['money_pressure_intrabucket_bp'])
        self.assertIsNone(factor['intrabucket_price_change_bp'])
        self.assertFalse(factor['score_authorized'])
        self.assertNotIn('KOSPI:000001',data['derived_issuer_money_factors'])
        self.assertEqual(validate_money_blob(gzip.compress(canonical(updated)),sessions[0],sessions[-1]),updated)
        for name in ('ACC_TRDVAL','TDD_CLSPRC','123456789'):
            self.assertNotIn(name,gzip.decompress(raw).decode())
        store.files[daily.repair_path(receipt['period'])] += b'x'
        with self.assertRaisesRegex(daily.MoneyRepairError,'digest_mismatch'):
            daily.overlay(store,blob,data,sessions,idx)

    def test_missing_values_stay_missing_and_changed_established_money_blocks(self):
        sessions, data, blob = original()
        receipt, _ = daily.source_additions(Client(amount=None),blob,data,sessions,
            {'KOSPI:000001'},{'requests':0})
        self.assertEqual(receipt['diagnostic']['unavailable_pairs'],1)
        changed = Client(); changed.changed = True
        with self.assertRaisesRegex(daily.MoneyRepairError,'established_source_changed'):
            daily.source_additions(changed,blob,data,sessions,{'KOSPI:000001'},{'requests':0})

    def test_proof_is_original_and_calendar_pinned_and_cannot_inject_price_fields(self):
        sessions, data, blob = original()
        d, _ = daily.source_additions(Client(),blob,data,sessions,{'KOSPI:000001'},{'requests':0})
        mutations = [lambda x:x.update(original_sha256='a'*64),
            lambda x:x.update(session_dates=list(reversed(sessions))),
            lambda x:x.update(price_or_direction_repaired=True),
            lambda x:x.update(investment_score_certified=True),
            lambda x:x['amount_factors']['KOSPI:000001'].update(intrabucket_price_change_bp=0),
            lambda x:x['amount_factors']['KOSPI:000001'].update(total_actual_amount_100k_krw=-1)]
        for mutate in mutations:
            bad=copy.deepcopy(d); mutate(bad)
            with self.assertRaises(daily.MoneyRepairError):
                daily.validate(gzip.compress(canonical(bad)),blob,data,sessions)
        bad=copy.deepcopy(d)
        bad['amount_factors']={'KOSPI:000002':next(iter(bad['amount_factors'].values()))}
        with self.assertRaises(daily.MoneyRepairError):
            daily.validate(gzip.compress(canonical(bad)),blob,data,sessions)

    def test_complete_source_replay_closes_persistence_without_fabricating_direction(self):
        store,days,forward,catalog,sessions=missing_fixture()
        original_bytes=store.files[money._path(sessions)]
        args=dict(asof=catalog['date'],catalog=catalog,source_revision='a'*40,
            days=days,digest='a'*64,forward_days=forward,week_loader=week_days)
        before=money.collect(store,**args)
        self.assertEqual(before['eligible_issuers'],0)
        result=daily.repair(store,store,Client(),catalog,max_windows=1,
            days=days,digest='a'*64,forward_days=forward)
        self.assertEqual(result['source_requests'],10)
        self.assertEqual(result['restored_issuer_cohorts'],1)
        after=money.collect(store,**args)
        self.assertEqual(after['eligible_issuers'],1)
        self.assertEqual(after['component_records']['000001']['component_observations']
            ['components']['persistence'][1],5)
        self.assertEqual(after['daily_repair_observation']['added_issuer_cohorts'],1)
        self.assertEqual(store.files[money._path(sessions)],original_bytes)
        for name in ('total_actual_amount_100k_krw','positive_amount_days','ACC_TRDVAL'):
            self.assertNotIn(name,json.dumps(after))
        again=daily.repair(store,store,Client(),catalog,max_windows=1,
            days=days,digest='a'*64,forward_days=forward)
        self.assertEqual(again['source_requests'],0)

    def test_unavailable_result_is_checkpointed_and_orphan_receipt_resumes(self):
        store,days,forward,catalog,sessions=missing_fixture()
        args=dict(days=days,digest='a'*64,forward_days=forward,max_windows=1)
        daily.repair(store,store,Client(amount=None),catalog,**args)
        again=daily.repair(store,store,Client(),catalog,**args)
        self.assertEqual(again['source_requests'],0)
        store,days,forward,catalog,sessions=missing_fixture()
        blob=store.files[money._path(sessions)]
        data=json.loads(gzip.decompress(blob))
        receipt,raw=daily.source_additions(Client(),blob,data,sessions,{'KOSPI:000001'},{'requests':0})
        store.files[daily.repair_path(receipt['period'])]=raw
        resumed=daily.repair(store,store,Client(),catalog,**args)
        self.assertEqual(resumed['source_requests'],0)
        self.assertEqual(resumed['restored_issuer_cohorts'],1)

    def test_shared_writer_retry_keeps_source_calls_bounded_and_daily_index_is_separate(self):
        store,days,forward,catalog,_=missing_fixture()
        original_put=store.put_new
        failures=[0]
        def contended(name,raw,message):
            if name.startswith(daily.PREFIX) and failures[0]<2:
                failures[0]+=1
                raise private_409()
            return original_put(name,raw,message)
        client=Client()
        with patch.object(store,'put_new',side_effect=contended),patch('time.sleep'):
            result=daily.repair(store,store,client,catalog,max_windows=1,
                days=days,digest='a'*64,forward_days=forward)
        self.assertEqual(result['source_requests'],10)
        self.assertEqual(len(client.calls),10)
        self.assertIn(daily.CHECKPOINT,store.files)
        from v3_krx_money_week_repair import CHECKPOINT as weekly_checkpoint
        self.assertNotIn(weekly_checkpoint,store.files)

    def test_source_retry_is_bounded_and_budget_has_no_provider_overrun(self):
        sessions,data,blob=original()
        from official_probe.clients import KRXProbeError
        client=Client(); call=client.daily_market; count=[0]
        def flaky(market,day):
            count[0]+=1
            if count[0]==1:raise KRXProbeError(market,'empty_market_rows',200)
            return call(market,day)
        budget={'requests':0}
        with patch.object(client,'daily_market',side_effect=flaky),patch('time.sleep'):
            daily.source_additions(client,blob,data,sessions,{'KOSPI:000001'},budget)
        self.assertEqual(budget['requests'],11)
        with self.assertRaises(daily.MoneyRepairError):
            daily.BudgetClient(client,{'requests':daily.MAX_CALLS}).daily_market('KOSPI',sessions[0])

    def test_period_and_index_validation_rejects_path_escape_and_bad_hashes(self):
        for label in ('../outside','2026-10-01_2026-10-02','2026-13-01_2026-13-07'):
            with self.assertRaises(daily.MoneyRepairError):daily.repair_path(label)
        self.assertEqual(daily.index(None),{'schema':daily.INDEX_SCHEMA,'windows':{}})
        with self.assertRaises(daily.MoneyRepairError):
            daily.index(canonical({'schema':daily.INDEX_SCHEMA,'windows':{
                '2026-10-26_2026-10-30':'not-a-hash'}}))

    def test_workflow_collects_both_families_and_retains_all_pending_writers(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'.github/workflows/v3-krx-money-week-repair.yml').read_text()
        self.assertIn('v3_krx_money_daily_repair.py --private-snapshot .money-private --max-windows 12',text)
        self.assertIn("steps.daily_recovery.outputs.continue_daily_catchup == 'true'",text)
        self.assertIn("steps.recovery.outputs.weekly_money_changed == 'true'",text)
        self.assertIn("steps.daily_recovery.outputs.daily_money_changed == 'true'",text)
        self.assertIn('v3-actual-money-score.yml/dispatches',text)
        self.assertIn('always() &&',text)
        for name in ('v3-krx-money-week-repair.yml','v3-krx-money-forward.yml',
                     'v3-krx-money-history.yml','v3-krx-calendar-week-money.yml'):
            self.assertIn('queue: max',(root/'.github/workflows'/name).read_text())
        for name in ('v3-actual-money-score.yml','v3-money-saved-structure-replay.yml'):
            view=(root/'.github/workflows'/name).read_text()
            self.assertIn('/derived/money-five-session-repairs/',view)
            self.assertIn('/derived/money-five-session-repair-checkpoint.json',view)

    def test_checkpoint_change_flag_survives_a_later_source_failure(self):
        from official_probe.clients import KRXProbeError
        store,days,forward,catalog,_=missing_fixture()
        windows,originals,_=money.read_daily_history(store,asof=catalog['date'],
            before={p:store.get_content(p) for p in (money.CHECKPOINT,money.FORWARD)},
            days=days,digest='a'*64,forward_days=forward)
        data=copy.deepcopy(originals[21])
        data['derived_issuer_money_factors'].pop('KOSPI:000001')
        data['complete_five_session_issuer_market_pairs']=0
        store.files[money._path(windows[21])]=gzip.compress(canonical(data))
        class FailingClient(Client):
            def daily_market(self,market,day):
                if len(self.calls)>=10:
                    raise KRXProbeError(market,'http_server_error',503)
                return super().daily_market(market,day)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'outputs'
            with patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}),patch('time.sleep'):
                with self.assertRaises(ValueError):
                    daily.repair(store,store,FailingClient(),catalog,max_windows=2,
                        days=days,digest='a'*64,forward_days=forward)
            self.assertIn('daily_money_changed=true',output.read_text())
            self.assertEqual(len(daily.index(store.files[daily.CHECKPOINT])['windows']),1)


if __name__=='__main__':unittest.main()
