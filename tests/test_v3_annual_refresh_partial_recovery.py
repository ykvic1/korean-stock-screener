"""Bounded source interruption recovery never fabricates a verified year."""
import copy
from datetime import date
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_v3_dart_bulk_history import FakeDART, FakeResponse
from test_v3_dart_weekly_verified_refresh import evidence, KEY
import v3_dart_bulk_history as bulk
import v3_dart_weekly_verified_refresh as rebind

DAY=date(2026,10,20)


class ReadTimeout(Exception):pass


class InterruptedDART(FakeDART):
    def __init__(self,kind='timeout',first_year=2023):
        super().__init__()
        self.kind=kind
        self.first_year=first_year

    def get(self,url,params,timeout):
        if url.endswith('corpCode.xml'):
            return super().get(url,params,timeout)
        year=int(params['bsns_year'])
        if year>=self.first_year:
            self.calls.append((url,params))
            if self.kind=='timeout':raise ReadTimeout('private URL/key must not be logged')
            return FakeResponse({'status':self.kind,'message':'private URL/key must not be logged'})
        result=super().get(url,params,timeout)
        for row in result.body.get('list',[]):row['rcept_no']=f'{year+1}0325000002'
        return result


class PartialRecoveryTests(unittest.TestCase):
    def execute(self,caller):
        self.market,self.catalog,self.book,self.history=evidence()
        return rebind.execute(self.market,self.catalog,self.book,self.history,
            when=DAY,part=0,mode='publish',key=KEY,session=caller)

    def test_timeout_publishes_only_completed_pairs_and_retains_unrequested_years(self):
        caller=InterruptedDART()
        report,updated,score=self.execute(caller)
        code=next(r['code'] for r in report['review_flags']
                  if r['reason']=='newer_official_source_reconciled')
        self.assertEqual(report['verified_company_year_count'],1)
        self.assertEqual(report['not_rechecked_company_year_count'],3)
        self.assertEqual(report['source_api_calls'],3) # map, successful FY, failed FY
        self.assertEqual(len(caller.calls),3)
        self.assertEqual(report['waiting_reason'],'waiting_annual_source_transport')
        self.assertFalse(report['source_slice_requests_completed'])
        self.assertFalse(report['historical_PIT_certified'])
        self.assertEqual(updated['records'][code]['years']['2022']['CFS']['receipt_id'],
                         '20230325000002')
        for year in ('2023','2024','2025'):
            self.assertEqual(updated['records'][code]['years'][year],
                             self.history['records'][code]['years'][year])
        self.assertEqual(score,bulk.research_parts(updated,self.catalog,self.book))
        self.assertNotIn('private URL',json.dumps(report))

    def test_quota_defers_without_retries_and_does_not_mark_missing(self):
        caller=InterruptedDART(kind='020',first_year=2022)
        report,updated,score=self.execute(caller)
        self.assertEqual(report['status'],'waiting_annual_source_quota')
        self.assertEqual(report['verified_company_year_count'],0)
        self.assertEqual(report['source_api_calls'],2)
        self.assertEqual(len(caller.calls),2)
        self.assertIsNone(updated)
        self.assertIsNone(score)
        self.assertFalse(report['source_archive_changed'])

    def test_budget_stops_without_counting_an_unissued_request(self):
        caller=InterruptedDART(first_year=2025)
        with patch.object(rebind,'MAX_REQUESTS',1):
            report,updated,_=self.execute(caller)
        self.assertEqual(report['waiting_reason'],'waiting_annual_source_budget')
        self.assertEqual(report['source_api_calls'],2)
        self.assertEqual(report['verified_company_year_count'],1)
        self.assertTrue(report['source_archive_changed'])

    def test_provider_schema_and_auth_failures_do_not_publish_partial_success(self):
        for status in ('unexpected-secret-value','010','013-invalid'):
            with self.subTest(status=status):
                caller=InterruptedDART(kind=status)
                with self.assertRaises(rebind.RebindError) as caught:self.execute(caller)
                report=caught.exception.report
                self.assertEqual(report['status'],'blocked_annual_source_validation')
                self.assertEqual(report['validated_pairs_discarded'],1)
                self.assertFalse(report['source_archive_changed'])
                self.assertNotIn(status,json.dumps(report))
                self.assertNotIn('private URL',json.dumps(report))

    def test_http_classification_does_not_hide_unauthorized_or_unknown_errors(self):
        for status,expected in ((429,'waiting_annual_source_rate_limit'),
                                (503,'waiting_annual_source_service'),(403,None)):
            error=bulk.BulkHistoryError('dart_bulk_http_status_failed')
            error.http_status=status
            self.assertEqual(rebind._source_wait_reason(error),expected)
        self.assertIsNone(rebind._source_wait_reason(bulk.BulkHistoryError('unknown')))

    def test_no_accounts_response_differs_from_an_unrequested_period(self):
        caller=InterruptedDART()
        caller.blank_year=2022
        report,updated,_=self.execute(caller)
        self.assertEqual(report['reason_counts']['now_missing_keep_previous'],1)
        self.assertEqual(report['reason_counts']['not_rechecked_keep_previous'],3)
        self.assertFalse(report['source_archive_changed'])
        self.assertEqual(updated['records'],self.history['records'])

    def test_partial_publication_remains_strictly_verifiable_without_provider(self):
        report,updated,score=self.execute(InterruptedDART())
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            args=['--mode','verify','--run-day',DAY.isoformat(),'--part','0']
            for name,value in zip(('market','catalog','book','history','scores'),
                (self.market,self.catalog,self.book,updated,score)):
                path=root/(name+'.json');path.write_text(json.dumps(value))
                args+=['--'+name,str(path)]
            self.assertEqual(rebind.main(args),0)
            path.write_text(json.dumps({**score,'companies_with_computable_pb_scores':-1}))
            with self.assertRaises(rebind.RebindError):rebind.main(args)

    def test_forged_verified_mask_is_rejected(self):
        market,catalog,book,history=evidence()
        _,selected=rebind.fresh.slice_codes(history['records'],DAY,0)
        observed={c:copy.deepcopy(history['records'][c]['years']) for c in selected}
        with self.assertRaises(rebind.RebindError):
            rebind.reconcile(market,catalog,book,history,observed,when=DAY,part=0,
                             verified_pairs={('999999','2025')})

    def test_main_failure_keeps_sources_and_saves_sanitized_review_report(self):
        market,catalog,book,history=evidence()
        score=bulk.research_parts(history,catalog,book)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            paths=[root/(name+'.json') for name in ('market','catalog','book','history','scores')]
            for path,value in zip(paths,(market,catalog,book,history,score)):
                path.write_text(json.dumps(value))
            before=[p.read_bytes() for p in paths]
            report=root/'review.json'
            args=['--mode','publish','--run-day',DAY.isoformat(),'--part','0',
                  '--report',str(report),'--company-cache',str(root/'absent.json')]
            for name,path in zip(('market','catalog','book','history','scores'),paths):
                args+=['--'+name,str(path)]
            transport=SimpleNamespace(Session=lambda:InterruptedDART(kind='010'))
            with patch.dict(os.environ,{'DART_API_KEY':KEY}), \
                 patch.dict(sys.modules,{'requests':transport}):
                with self.assertRaises(rebind.RebindError):rebind.main(args)
            self.assertEqual(before,[p.read_bytes() for p in paths])
            saved=json.loads(report.read_text())
            self.assertEqual(saved['status'],'blocked_annual_source_validation')
            self.assertNotIn(KEY,report.read_text())
