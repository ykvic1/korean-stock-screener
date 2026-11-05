import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_dart_structured_action_continuation as c
import v3_private_dart_structured_action_receipts as a
from test_v3_krx_derived_5day import FakePrivate


class Store(FakePrivate):
    def transport(self,method,path,body):
        assert method=='PUT' and path=='contents/'+a.CHECKPOINT
        assert body['sha']=='sha'
        self.blobs[a.CHECKPOINT]=base64.b64decode(body['content'])


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.store=Store()
        self.store.blobs[a.TRIAGE_CHECKPOINT]=b'opaque triage'
        self.store.blobs[a.triage_path(0)]=b'opaque immutable queue'
        self.records=[{'rcept_no':f'2016110100000{i}', 'corp_code':'00121932','title_hints':['fixture']}
                      for i in range(1,4)]
        self.patches=[patch.object(a,'triage_cursor',return_value={'next_pair_index':1}),
                      patch.object(a,'_candidate_queue',return_value=(self.records,'b'*64,3))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.calls=[]
    def fetch(self,*args):
        self.calls.append(args)
        return {'status':'013'}
    def test_real_transaction_continues_and_stops_at_upstream_without_score_promotion(self):
        events=[]
        report=c.continuous(self.store,self.fetch,emit=events.append)
        self.assertEqual(report['processed_candidates'],3)
        self.assertEqual(report['provider_requests'],9)
        self.assertEqual(report['completed_batches'],2)
        self.assertEqual(report['completed_month_pairs'],1)
        self.assertEqual(report['stop_reason'],'waiting_for_upstream')
        self.assertEqual(len(events),2)
        self.assertFalse(report['effective_dates_certified'])
        self.assertEqual(report['scores_promoted'],0)
        for forbidden in ('corp_code','rcept_no','reported_date','raw_source','00121932'):
            self.assertNotIn(forbidden,json.dumps(report))
    def test_six_call_ceiling_and_restart_continue_without_requerying_processed_records(self):
        first=c.continuous(self.store,self.fetch,max_requests=6)
        self.assertEqual(first['provider_requests'],6)
        self.assertEqual(first['processed_candidates'],2)
        self.assertEqual(first['stop_reason'],'provider_budget')
        second=c.continuous(self.store,self.fetch)
        self.assertEqual(second['provider_requests'],3)
        self.assertEqual(len(self.calls),9)
        self.assertEqual(second['processed_candidates'],1)
    def test_failed_attempt_counts_and_never_expands_six_call_transaction(self):
        fail=[True]
        def fetch(*args):
            self.calls.append(args)
            if fail[0]:
                fail[0]=False
                raise a.StructuredActionError('official_structured_action_transport_or_json_failed')
            return {'status':'013'}
        with patch.object(a.time,'sleep'):
            report=c.continuous(self.store,fetch,max_requests=6)
        self.assertEqual(report['provider_requests'],4)
        self.assertEqual(report['processed_candidates'],1)
        self.assertEqual(report['stop_reason'],'provider_budget')
    def test_deadline_orphan_is_not_committed_and_resumes_with_zero_duplicate_fetch(self):
        now=[0]
        def fetch(*args):
            body=self.fetch(*args)
            if len(self.calls)==3:now[0]=5
            return body
        first=c.continuous(self.store,fetch,clock=lambda:now[0],max_seconds=5)
        self.assertEqual(first['stop_reason'],'time_limit')
        self.assertEqual(first['provider_requests'],3)
        self.assertNotIn(a.CHECKPOINT,self.store.blobs)
        self.assertIn(a._path(0,self.records[0]['rcept_no']),self.store.blobs)
        second=c.continuous(self.store,self.fetch)
        self.assertEqual(second['provider_requests'],6)
        self.assertEqual(second['previously_saved_reports_reused'],1)
        self.assertEqual(len(self.calls),9)
    def test_quota_and_auth_failure_stop_without_becoming_negative_action_evidence(self):
        with self.assertRaises(a.StructuredActionError):
            c.continuous(self.store,lambda *args:{'status':'020'})
        self.assertNotIn(a.CHECKPOINT,self.store.blobs)
    def test_stalled_or_falsely_certified_batch_is_rejected(self):
        def corrupt(storage,fetch,**kwargs):
            result=a.run(storage,fetch,**kwargs)
            result['corporate_action_effective_dates_certified']=True
            return result
        with self.assertRaises(c.ActionContinuationError):
            c.continuous(self.store,self.fetch,collector=corrupt)
    def test_external_cursor_change_between_batches_stops_without_additional_provider_calls(self):
        def change(event):self.store.blobs.pop(a.CHECKPOINT,None)
        with self.assertRaisesRegex(c.ActionContinuationError,'cursor_changed_between_batches'):
            c.continuous(self.store,self.fetch,emit=change)
        self.assertEqual(len(self.calls),6)
    def test_batch_and_time_limits_and_invalid_budget_rejected(self):
        report=c.continuous(self.store,self.fetch,max_batches=1)
        self.assertEqual(report['completed_batches'],1)
        self.assertEqual(report['stop_reason'],'batch_limit')
        for values in ({'max_batches':c.MAX_BATCHES+1},{'max_batches':True},{'max_requests':c.MAX_REQUESTS+1},
                       {'max_requests':5},{'max_seconds':3301},{'max_seconds':0}):
            with self.assertRaises(c.ActionContinuationError):c.continuous(self.store,self.fetch,**values)
    def test_empty_queue_advances_without_provider_calls(self):
        with patch.object(a,'_candidate_queue',return_value=([],'b'*64,0)):
            report=c.continuous(self.store,self.fetch)
        self.assertEqual(report['provider_requests'],0)
        self.assertEqual(report['completed_month_pairs'],1)
        self.assertEqual(report['stop_reason'],'waiting_for_upstream')

    def test_accelerated_run_crosses_old_cap_and_resumes_without_duplicate_calls(self):
        # A busy pair must survive many independent CAS transactions, not just
        # a higher config value. Stop at 128 batches, then resume its tail.
        records=[{'rcept_no':f'20161101{i:06d}', 'corp_code':'00121932','title_hints':['fixture']}
                 for i in range(1,514)]
        with patch.object(a,'_candidate_queue',return_value=(records,'b'*64,len(records))):
            first=c.continuous(self.store,self.fetch,max_batches=128)
            self.assertEqual(first['processed_candidates'],256)
            self.assertEqual(first['provider_requests'],768)
            self.assertEqual(first['stop_reason'],'batch_limit')
            second=c.continuous(self.store,self.fetch)
        self.assertEqual(second['processed_candidates'],257)
        self.assertEqual(second['provider_requests'],771)
        self.assertEqual(second['completed_month_pairs'],1)
        self.assertEqual(len(self.calls),513*3)
        self.assertEqual(second['scores_promoted'],0)

    def test_accelerated_run_still_stops_at_deadline_after_old_cap(self):
        records=[{'rcept_no':f'20161101{i:06d}', 'corp_code':'00121932','title_hints':['fixture']}
                 for i in range(1,301)]
        now=[0]
        def emit(event):
            if event['completed_batches']==65:now[0]=5
        with patch.object(a,'_candidate_queue',return_value=(records,'b'*64,len(records))):
            result=c.continuous(self.store,self.fetch,clock=lambda:now[0],max_seconds=5,emit=emit)
        self.assertEqual(result['completed_batches'],65)
        self.assertEqual(result['stop_reason'],'time_limit')
        self.assertEqual(result['provider_requests'],390)
    def test_complete_cursor_requires_existing_tail_and_never_claims_effect_dates(self):
        with patch.object(c,'TARGET_PAIRS',1):
            result=c.continuous(self.store,self.fetch)
            self.assertEqual(result['stop_reason'],'complete')
            again=c.continuous(self.store,self.fetch)
            self.assertEqual(again['provider_requests'],0)
            self.assertEqual(again['stop_reason'],'complete')
            state=a._cursor(self.store.blobs[a.CHECKPOINT])
            self.store.blobs[state['last_report_file']]=b'corrupt'
            with self.assertRaises(a.StructuredActionError):c.continuous(self.store,self.fetch)
    def test_bounded_cursor_read_lag_does_not_repeat_official_requests(self):
        original=self.store.get_content
        lag=[]
        def get(path):
            if path==a.CHECKPOINT and lag:return lag.pop(0),'sha'
            return original(path)
        self.store.get_content=get
        def collect(storage,fetch,**kwargs):
            before=original(a.CHECKPOINT)[0]
            result=a.run(storage,fetch,**kwargs)
            if result['status']=='private_structured_action_receipt_research':lag.extend([before,before])
            return result
        with patch.object(c.time,'sleep'):
            result=c.continuous(self.store,self.fetch,collector=collect)
        self.assertEqual(result['processed_candidates'],3)
        self.assertEqual(result['provider_requests'],9)
    def test_stalled_cursor_and_attempts_over_hard_budget_cannot_loop(self):
        def stalled(storage,fetch,**kwargs):
            return dict(status='private_structured_action_receipt_research',provider_calls=0,
                processed_candidates=0,previously_saved_reports_reused=0,
                exact_receipt_categories_matched=0,reported_schedule_date_candidates_observed=0,
                corporate_action_effective_dates_certified=False,adjusted_prices_certified=False,
                scores_promoted=0,public_issuer_records_written=0,next_pair_index=0)
        with self.assertRaisesRegex(c.ActionContinuationError,'cursor_stalled'):
            c.continuous(self.store,self.fetch,collector=stalled)
        def excessive(storage,fetch,**kwargs):
            for _ in range(7):fetch('fixture','fixture','fixture','fixture')
        result=c.continuous(self.store,self.fetch,collector=excessive,max_requests=6)
        self.assertEqual(result['provider_requests'],6)
        self.assertEqual(len(self.calls),6)
        self.assertEqual(result['stop_reason'],'provider_budget')

    def test_workflow_offline_has_no_secrets_or_writer_lock(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-dart-structured-action-continuation.yml').read_text()
        offline,live=text.split('  private-continuation:',1)
        self.assertNotIn('secrets.',offline);self.assertNotIn('concurrency:',offline)
        self.assertIn('needs: offline',live)
        self.assertIn('group: v3-dart-private-structured-action-receipts',live)
        self.assertNotIn('upload-artifact',text);self.assertNotIn('git add',text)
        self.assertIn("options: [dry_run, live]",text)
        self.assertIn("cron: '17 1 * * *'",text)
        self.assertIn("cron: '55 2,6,10,14,18,22 * * *'",text)
        self.assertIn("default: '256'",text)
        self.assertIn("|| '256'",live)
        self.assertIn('queue: max',live)
        self.assertIn('timeout-minutes: 65',live)


if __name__=='__main__':unittest.main()
