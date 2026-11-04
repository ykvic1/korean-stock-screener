import copy
from datetime import date, timedelta
from hashlib import sha256
import gzip
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from official_probe.clients import KRXProbeError
from test_v3_krx_money_backfill import FakePrivateStore, private_409
from v3_krx_calendar_week_money import (derive, validate, collect, manifest,
    closed_week_end, path, CHECKPOINT, WeeklyMoneyError, canonical)


def days(end):
    stop=date.fromisoformat(end)
    return [(stop-timedelta(days=i)).isoformat() for i in range(6,1,-1)]


class Client:
    def __init__(self): self.calls=[];self.fail_once=False;self.missing=False
    def daily_market(self,market,day):
        self.calls.append((market,day))
        if self.fail_once:
            self.fail_once=False
            raise KRXProbeError(market,'empty_body',200)
        row={'BAS_DD':day.replace('-',''),'ISU_CD':'000001','TDD_OPNPRC':'100',
             'TDD_HGPRC':'101','TDD_LWPRC':'99','TDD_CLSPRC':'100','ACC_TRDVOL':'9999'}
        if not self.missing: row['ACC_TRDVAL']='123456789'
        return [row],json.dumps({'OutBlock_1':[row]}).encode()


class CalendarMoneyTests(unittest.TestCase):
    def test_complete_anchor_is_idempotent_and_next_week_appends(self):
        store=FakePrivateStore();client=Client()
        with patch('v3_krx_calendar_week_money.HISTORY_WEEKS',2):
            collect(store,client,asof='2026-11-04',max_weeks=2,day_loader=days)
            before=len(client.calls)
            repeat=collect(store,client,asof='2026-11-04',max_weeks=2,day_loader=days)
            self.assertEqual(repeat['source_requests'],0)
            self.assertEqual(len(client.calls),before)
            forward=collect(store,client,asof='2026-11-09',max_weeks=2,day_loader=days)
            self.assertEqual(forward['history_completed'],2)
            self.assertEqual(forward['forward_completed'],1)
            state=json.loads(store.files[CHECKPOINT])
            self.assertEqual(state['anchor_week_end'],'2026-11-01')
            self.assertEqual(state['tail_week_end'],'2026-11-08')

    def test_offline_workflow_cannot_access_secrets_or_hold_live_writer_lock(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-krx-calendar-week-money.yml').read_text()
        offline,live=text.split('  private-weeks:',1)
        self.assertNotIn('secrets.',offline)
        self.assertNotIn('concurrency:',offline)
        self.assertIn('needs: offline-safety',live)
        self.assertIn('group: v3-krx-actual-money-private-derived-backfill',live)
        self.assertNotIn('upload-artifact',text)
        self.assertNotIn('git add',text)

    def test_midweek_excludes_open_week_but_friday_eod_and_weekend_are_complete(self):
        self.assertEqual(closed_week_end('2026-11-04'),'2026-11-01')
        self.assertEqual(closed_week_end('2026-11-06'),'2026-11-08')
        self.assertEqual(closed_week_end('2026-11-08'),'2026-11-08')
        self.assertEqual(closed_week_end('2026-11-09'),'2026-11-08')

    def test_holiday_short_week_uses_actual_sessions_and_never_close_times_volume(self):
        sessions=['2026-10-29','2026-10-30']
        client=Client();budget={'requests':0}
        data,blob=derive(client,'2026-11-01',sessions,budget)
        self.assertEqual(budget['requests'],4)
        self.assertEqual(data['issuer_week_factors']['KOSPI:000001'],
                         {'amount_100k_krw':2469,'observed_sessions':2})
        self.assertEqual(validate(blob,'2026-11-01',sessions),data)
        for raw in ('TDD_CLSPRC','ACC_TRDVAL','ACC_TRDVOL','123456789'):
            self.assertNotIn(raw,gzip.decompress(blob).decode())

    def test_calendar_crossing_and_missing_real_value_are_not_filled(self):
        with self.assertRaises(WeeklyMoneyError):
            derive(Client(),'2026-11-01',['2026-10-23','2026-10-30'],{'requests':0})
        client=Client();client.missing=True
        with self.assertRaises(WeeklyMoneyError):
            derive(client,'2026-11-01',days('2026-11-01'),{'requests':0})

    def test_only_transient_reads_retry_and_count_toward_request_budget(self):
        client=Client();client.fail_once=True;budget={'requests':0}
        with patch('v3_krx_calendar_week_money.time.sleep'):
            derive(client,'2026-11-01',days('2026-11-01'),budget)
        self.assertEqual(budget['requests'],11)
        with self.assertRaises(WeeklyMoneyError):
            derive(Client(),'2026-11-01',days('2026-11-01'),{'requests':3000})

    def test_empty_market_failure_has_bounded_retry_and_safe_source_diagnostics(self):
        client=Client()
        with patch.object(client,'daily_market',side_effect=KRXProbeError('KOSPI','empty_market_rows',200)) as call, patch(
            'v3_krx_calendar_week_money.time.sleep'):
            with self.assertRaises(WeeklyMoneyError) as caught:
                derive(client,'2026-11-01',days('2026-11-01'),{'requests':0})
        self.assertEqual(call.call_count,3)
        self.assertEqual(caught.exception.safe_krx_reason,'empty_market_rows')
        self.assertEqual(caught.exception.safe_source_market,'KOSPI')
        self.assertEqual(caught.exception.safe_source_day,'2026-10-26')
        self.assertEqual(str(caught.exception),'official_weekly_money_source_unavailable')

    def test_new_cutoff_cannot_reinterpret_fixed_historical_anchor(self):
        store=FakePrivateStore();client=Client()
        a=collect(store,client,asof='2026-11-04',max_weeks=2,day_loader=days)
        self.assertEqual(a['history_completed'],2)
        state=json.loads(store.files[CHECKPOINT]);self.assertEqual(state['anchor_week_end'],'2026-11-01')
        b=collect(store,client,asof='2026-11-11',max_weeks=1,day_loader=days)
        self.assertEqual(b['history_completed'],3)
        state=json.loads(store.files[CHECKPOINT]);self.assertEqual(state['tail_week_end'],'2026-10-18')
        self.assertEqual(state['anchor_week_end'],'2026-11-01')

    def test_orphan_aggregate_is_reused_without_new_provider_calls(self):
        store=FakePrivateStore();client=Client()
        data,blob=derive(client,'2026-11-01',days('2026-11-01'),{'requests':0})
        store.files[path('2026-11-01')]=blob
        before=len(client.calls)
        result=collect(store,client,asof='2026-11-04',max_weeks=1,day_loader=days)
        self.assertEqual(result['source_requests'],0)
        self.assertEqual(len(client.calls),before)

    def test_immutable_and_checkpoint_409_reconcile_exact_content(self):
        for target in ('immutable','cursor'):
            class Store(FakePrivateStore):
                failed=False
                def put_new(self,name,raw,message):
                    super().put_new(name,raw,message)
                    if not self.failed and (name==CHECKPOINT)==(target=='cursor'):
                        self.failed=True
                        raise private_409()
            store=Store();client=Client()
            with self.subTest(target=target):
                result=collect(store,client,asof='2026-11-04',max_weeks=1,day_loader=days)
                self.assertEqual(result['history_completed'],1)
                self.assertEqual(len(client.calls),10)

    def test_corrupt_tail_unknown_fields_and_false_calendar_are_rejected(self):
        _,blob=derive(Client(),'2026-11-01',days('2026-11-01'),{'requests':0})
        data=json.loads(gzip.decompress(blob))
        for key,value in (('original_daily_amounts',[1,2]),('calendar_week_grouping_verified',False)):
            bad={**data,key:value}
            with self.assertRaises(WeeklyMoneyError):
                validate(gzip.compress(canonical(bad)),'2026-11-01',days('2026-11-01'))
        store=FakePrivateStore()
        collect(store,Client(),asof='2026-11-04',max_weeks=1,day_loader=days)
        store.files[path('2026-11-01')]=b'changed'
        with self.assertRaises(WeeklyMoneyError):
            collect(store,Client(),asof='2026-11-04',max_weeks=1,day_loader=days)


if __name__=='__main__':unittest.main()
