"""Recover only unqueried BS gaps in annual-book seeds; no invented assets."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import v3_dart_bulk_history as b
import test_v3_dart_bulk_history as fixtures


class SeedBalanceTests(unittest.TestCase):
    def fixture(self):
        market,catalog=fixtures.fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            prior,_=b.collect(market,catalog,None,key='synthetic-test-not-credential',session=fixtures.FakeDART())
        for row in prior['records'].values():
            ref=row['years']['2025']['CFS']
            ref['values']['assets']=None
            ref['source_origin']='previously_observed_dart_annual_book'
        _,members=b._inputs(market,catalog)
        cache=b.make_company_mapping_cache(fixtures.CORPS,catalog['date'],members)
        return market,catalog,prior,cache

    def collect(self,source=None,**kw):
        market,catalog,prior,cache=self.fixture()
        provider=source or fixtures.FakeDART()
        with contextlib.redirect_stdout(io.StringIO()):
            result,calls=b.collect(market,catalog,prior,key='synthetic-test-not-credential',session=provider,
                company_cache=cache,recover_seed_balance=True,**kw)
        return market,catalog,prior,result,calls,provider

    def test_opt_in_queries_latest_seed_year_once_and_preserves_known_values(self):
        market,catalog,prior,result,calls,source=self.collect()
        self.assertEqual(calls,1)
        self.assertEqual([p['bsns_year'] for _,p in source.calls],['2025'])
        self.assertFalse(b.plan(market,catalog,prior=prior)[-1])
        self.assertTrue(b.plan(market,catalog,prior=prior,recover_seed_balance=True)[-1])
        for code,old in prior['records'].items():
            new=result['records'][code]
            for year in ('2022','2023','2024'):self.assertEqual(new['years'][year],old['years'][year])
            saved=new['years']['2025']['CFS'];seed=old['years']['2025']['CFS']
            for k,v in seed['values'].items():
                if v is not None:self.assertEqual(saved['values'][k],v)
            self.assertEqual(saved['values']['assets'],90_000_000_000)
            self.assertEqual(saved['bulk_balance_probe']['result'],'enriched')
        with contextlib.redirect_stdout(io.StringIO()):
            again,n=b.collect(market,catalog,result,key=None,session=None,recover_seed_balance=True)
        self.assertEqual(n,0)
        self.assertEqual(again['records'],result['records'])
        self.assertFalse(again['historical_PIT_source_certified'])

    def test_blank_revised_foreign_and_conflicting_sources_preserve_seed_and_stop_requery(self):
        for mode,expected in [('blank','source_scope_absent'),('revision','receipt_mismatch'),
                ('foreign','source_scope_absent'),('conflict','existing_balance_conflict')]:
            class Source(fixtures.FakeDART):
                def get(self,url,params,timeout):
                    response=super().get(url,params,timeout)
                    if 'corpCode.xml' in url:return response
                    if mode=='blank':return fixtures.FakeResponse({'status':'013'})
                    for row in response.body['list']:
                        if mode=='revision':row['rcept_no']='20260319000001'
                        if mode=='foreign':row['currency']='USD'
                        if mode=='conflict':
                            if row['account_nm']=='부채총계':row['thstrm_amount']='41000000000'
                            if row['account_nm']=='자본총계':row['thstrm_amount']='49000000000'
                    return response
            with self.subTest(mode=mode):
                market,catalog,prior,result,calls,_=self.collect(Source())
                self.assertEqual(calls,1)
                for code,old in prior['records'].items():
                    new=result['records'][code]['years']['2025']['CFS']
                    self.assertEqual(new['values'],old['years']['2025']['CFS']['values'])
                    self.assertEqual(new['bulk_balance_probe']['result'],expected)
                self.assertFalse(b.plan(market,catalog,prior=result,recover_seed_balance=True)[-1])

    def test_safe_budget_returns_only_verified_checkpoint_and_resumes_other_seeds(self):
        market,catalog,prior,cache=self.fixture()
        with patch.object(b,'MAX_CORPS',2),patch.object(b,'MAX_SOURCE_FISCAL_CALLS',1),contextlib.redirect_stdout(io.StringIO()):
            result,calls=b.collect(market,catalog,prior,key='synthetic-test-not-credential',
                session=fixtures.FakeDART(),company_cache=cache,recover_seed_balance=True)
        self.assertEqual(calls,1)
        completed=sum(r['years']['2025']['CFS']['values']['assets'] is not None for r in result['records'].values())
        self.assertEqual(completed,2)
        self.assertEqual(sum(map(len,b.plan(market,catalog,prior=result,recover_seed_balance=True)[-1])),3)
        self.assertTrue(all(prior['records'][c]['years']['2025']['CFS']['values']['assets'] is None for c in prior['records']))

    def test_ordinary_saved_years_and_invalid_markers_cannot_authorize_overwrite(self):
        market,catalog,prior,_=self.fixture();code=next(iter(prior['records']))
        old=copy.deepcopy(prior['records'][code]['years']['2025'])
        incoming=copy.deepcopy(old);incoming['CFS']['values']['assets']=90_000_000_000
        old['CFS'].pop('source_origin')
        with self.assertRaises(b.BulkHistoryError):b._merge_seed_balance_year(old,incoming,2025,catalog['date'])
        old=copy.deepcopy(prior['records'][code]['years']['2025'])
        old['CFS']['bulk_balance_probe']={'receipt_id':old['CFS']['receipt_id'],'result':'arbitrary'}
        with self.assertRaises(b.BulkHistoryError):b._seed_balance_pending(old,2025,catalog['date'])

    def test_real_saved_census_has_all_market_seed_gaps_not_just_near_complete_issuers(self):
        root=Path(__file__).resolve().parents[1]/'docs'
        read=lambda n:json.loads((root/n).read_text())
        market,catalog,prior=[read(n) for n in ('data.json','v22-market-catalog.json','v3-dart-bulk-history.json')]
        day,_,_,fiscal,cohorts=b.plan(market,catalog,prior=prior,recover_seed_balance=True)
        targets={c for cohort in cohorts for c in cohort}
        expected={c for c,row in prior['records'].items() if any(
            b._seed_balance_pending(row['years'].get(str(y)),y,day) for y in fiscal)}
        self.assertTrue(targets<=expected)
        self.assertEqual(len(targets),min(300,len(expected)))
        self.assertTrue(all(len(x)<=100 for x in cohorts))

    def test_synthetic_same_receipt_assets_replay_into_fcf_and_full_model_without_loss(self):
        from v3_real_six_pillar_coverage_audit import coverage
        from v3_current_observed_scorecard import main as publish
        from v3_near_100_observed_gap import main as gap_audit
        import v3_operational_research_score as ops
        root=Path(__file__).resolve().parents[1]/'docs'
        read=lambda n:json.loads((root/n).read_text())
        market,catalog,prior,book=[read(n) for n in ('data.json','v22-market-catalog.json',
            'v3-dart-bulk-history.json','v3-dart-book-reference.json')]
        day=catalog['date'];updates={}
        for c,row in prior['records'].items():
            year=row['years'].get('2025',{})
            chosen={}
            for scope,ref in year.items():
                if not b._seed_balance_pending({scope:ref},2025,day):continue
                v=ref['values'];eq=v.get('equity');liab=v.get('liabilities')
                if type(eq) is not int or type(liab) is not int or liab+eq<=0:continue
                # Explicitly synthetic provider response for integration QA;
                # no actual public source values are fabricated or written.
                chosen[scope]={'receipt_id':ref['receipt_id'],'ambiguous':[],
                    'values':{**v,'assets':eq+liab}}
            if chosen:updates[c]={'2025':chosen}
        if not updates:self.skipTest('all annual-book seeds already checked')
        upgraded=b.partial_year_checkpoint(market,catalog,prior,updates)
        parts=b.research_parts(upgraded,catalog,book)
        audit=coverage(catalog,upgraded,parts,read('v3-weekly-rotation.json'),allow_lagged_weekly=True)
        mutable={'v3-dart-bulk-history.json','v3-dart-bulk-provisional.json',
            'v3-real-source-six-area-coverage.json','v3-personal-adjusted-scores.csv',
            'v3-personal-adjusted-score-summary.json','v3-current-observed-all-listed.csv',
            'v3-current-observed-all-listed-summary.json','v3-issuer-last-observed-factors.json',
            'v3-near-100-evidence-priority.json','v3-operational-research-scores.json',
            'v3-operational-research-scores.csv','v3-operational-research-comparison.json'}
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)
            for path in root.iterdir():
                if not path.is_file():continue
                if path.name in mutable:(target/path.name).write_bytes(path.read_bytes())
                else:(target/path.name).symlink_to(path)
            for name,value in (('v3-dart-bulk-history.json',upgraded),
                ('v3-dart-bulk-provisional.json',parts),('v3-real-source-six-area-coverage.json',audit)):
                (target/name).write_text(json.dumps(value))
            with contextlib.redirect_stdout(io.StringIO()):
                publish(['--docs',folder,'--mode','write','--refresh-derived'])
                gap_audit(['--docs',folder,'--mode','write'])
                with patch('sys.argv',['score','--docs',folder]):ops.main()
                publish(['--docs',folder,'--mode','verify','--refresh-derived'])
            after=json.loads((target/'v3-operational-research-scores.json').read_text())
        before=read('v3-operational-research-scores.json')
        self.assertTrue(all(r['available']>=before['rows'][c]['available']-.000001 for c,r in after['rows'].items()))
        self.assertGreater(sum(r['available']>=100 for r in after['rows'].values()),
            sum(r['available']>=100 for r in before['rows'].values()))
        self.assertFalse(after['investment_score_certified'])
