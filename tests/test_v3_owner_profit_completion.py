import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import v3_owner_profit_completion as completion
import v3_dart_2025_owner_profit_probe as proof
from v3_dart_bulk_history import make_company_mapping_cache

ROOT=Path(__file__).resolve().parents[1]/'docs'
NAMES=['v22-market-catalog.json','v3-dart-book-reference.json',
    'v3-near-100-evidence-priority.json','v3-dart-owner-profit-supplement.json',
    'v3-operational-research-scores.json','v3-operational-research-comparison.json',
    'v3-dart-reported-eps.json']


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs=[json.loads((ROOT/n).read_text()) for n in NAMES]

    def test_extension_preserves_previous_values_dates_and_acquisition_cohort(self):
        inputs=copy.deepcopy(self.inputs);before=copy.deepcopy(inputs)
        priority,source,preferred,summary=completion.extend(*inputs)
        self.assertTrue(set(inputs[2]['official_2025_owners_profit_missing_priority_codes'])<=
                        set(priority['official_2025_owners_profit_missing_priority_codes']))
        self.assertEqual(source['records'],inputs[3]['records'])
        self.assertEqual(inputs,before)
        self.assertTrue(proof.verify(source,inputs[0],inputs[1],priority))
        self.assertEqual(summary['provider_calls'],0)
        self.assertEqual(summary['points_added'],0)
        self.assertEqual(len(preferred-set(source['records'])),summary['pending_one_gap_issuers'])

    def test_forged_coverage_and_incomplete_eps_cannot_admit_a_target(self):
        bad=copy.deepcopy(self.inputs)
        bad[5]['operational_coverage']['available_100_count']+=1
        with self.assertRaises(proof.ProfitProbeError):completion.extend(*bad)
        lag=copy.deepcopy(self.inputs);lag[6]['source_sha256']['annual_book']='f'*64
        expanded,_,preferred,summary=completion.extend(*lag)
        self.assertEqual(summary['eps_preference_status'],'waiting_current_eps_receipts')
        self.assertFalse(preferred)
        self.assertEqual(summary['added'],completion.extend(*self.inputs)[3]['added'])

    def simulated_source(self):
        catalog,book,old_priority,prior,*_=self.inputs
        priority,rebound,preferred,summary=completion.extend(*self.inputs)
        day,members,expected,retained,selected=proof.plan(catalog,book,priority,rebound)
        mapping={code:f'{10000000+i:08d}' for i,code in enumerate(sorted(members))}
        reverse={v:k for k,v in mapping.items()}
        pending=set(selected)
        class Response:
            def __init__(self,payload):self.payload=payload
            def raise_for_status(self):pass
            def json(self):return self.payload
        class Session:
            def __init__(self):self.codes=[]
            def get(self,url,params,timeout):
                code=reverse[params['corp_code']]
                if code not in pending:raise AssertionError('unrequested or already queried receipt')
                self.codes.append(code);base=expected[code]
                value=0 if len(self.codes)==1 else -123456789 if len(self.codes)==2 else 123456789
                account=sorted(proof.PROFIT_IDS[base['fs_div']])[0]
                return Response({'status':'000','list':[{'corp_code':mapping[code],
                    'stock_code':code,'bsns_year':'2025','reprt_code':'11011',
                    'fs_div':base['fs_div'],'sj_div':'IS','account_detail':'-',
                    'rcept_no':base['annual_receipt_id'],'currency':'KRW',
                    'account_id':account,'thstrm_amount':str(value)}]})
        caller=Session()
        cache=make_company_mapping_cache(mapping,day,members)
        source,calls=proof.collect(catalog,book,priority,rebound,key='synthetic-key-not-a-secret',
            session=caller,cache=cache,preferred_codes=preferred)
        return priority,source,pending,caller,calls

    def test_only_unqueried_receipts_are_requested_and_zero_loss_are_observed(self):
        priority,source,pending,caller,calls=self.simulated_source()
        self.assertEqual(set(caller.codes),pending)
        self.assertEqual(calls,len(pending))
        self.assertEqual(source['records'].keys(),set(self.inputs[3]['records'])|pending)
        self.assertTrue(all(source['records'][c]['status']=='observed_owner_profit' for c in pending))
        self.assertEqual([source['records'][c] for c in self.inputs[3]['records']],list(self.inputs[3]['records'].values()))
        self.assertTrue(proof.verify(source,self.inputs[0],self.inputs[1],priority))

    def test_repeated_run_does_not_requery_completed_receipts(self):
        priority,source,pending,_,_=self.simulated_source()
        class NoProvider:
            def get(self,*a,**k):raise AssertionError('repeat query')
        priority=copy.deepcopy(priority)
        priority['official_2025_owners_profit_missing_priority_codes']=sorted(source['records'])
        priority['official_2025_owners_profit_missing_priority_count']=len(source['records'])
        source=proof.snapshot(source['market_asof'],self.inputs[0],self.inputs[1],priority,source['records'])
        same,calls=proof.collect(self.inputs[0],self.inputs[1],priority,source,
            key=None,session=NoProvider(),preferred_codes=pending)
        self.assertEqual(calls,0)
        self.assertEqual(same,source)

    def test_refresh_updates_only_provenance_and_stays_offline(self):
        priority,source,_,_,_=self.simulated_source()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in zip(NAMES[:4],[self.inputs[0],self.inputs[1],priority,source]):
                (root/name).write_text(json.dumps(value))
            self.assertIsNone(proof.main(['--docs',str(root),'--mode','refresh']))
            updated=json.loads((root/NAMES[3]).read_text())
            self.assertEqual(updated['records'],source['records'])
            self.assertTrue(proof.verify(updated,self.inputs[0],self.inputs[1],priority))

    def test_frozen_source_queue_survives_scoring_and_full_observation_can_increase(self):
        from v3_current_observed_scorecard import main as publish
        from v3_near_100_observed_gap import main as gap_audit
        import v3_operational_research_score as ops
        priority,source,pending,_,_=self.simulated_source()
        outputs={'v3-personal-adjusted-scores.csv','v3-personal-adjusted-score-summary.json',
            'v3-current-observed-all-listed.csv','v3-current-observed-all-listed-summary.json',
            'v3-issuer-last-observed-factors.json',*NAMES[2:6],
            'v3-operational-research-scores.csv'}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for path in ROOT.iterdir():
                if path.is_file():
                    if path.name in outputs:(root/path.name).write_bytes(path.read_bytes())
                    else:(root/path.name).symlink_to(path)
            (root/NAMES[2]).write_text(json.dumps(priority))
            (root/NAMES[3]).write_text(json.dumps(source))
            with contextlib.redirect_stdout(io.StringIO()):
                publish(['--docs',str(root),'--mode','write','--refresh-derived'])
                gap_audit(['--docs',str(root),'--mode','write'])
                with patch('sys.argv',['score','--docs',str(root)]):ops.main()
            report=json.loads((root/NAMES[4]).read_text())
            improvements=0
            for code in pending:
                before=self.inputs[4]['rows'][code]
                after=report['rows'][code]
                self.assertGreaterEqual(after['available'],before['available'])
                improvements+=after['available']>before['available']
            if pending:self.assertGreater(improvements,0)
            self.assertGreaterEqual(sum(r['available']==100 for r in report['rows'].values()),
                sum(r['available']==100 for r in self.inputs[4]['rows'].values()))
            self.assertEqual(json.loads((root/NAMES[2]).read_text()),priority)
            self.assertFalse(report['investment_score_certified'])

    def test_multiple_gap_issuers_are_admitted_without_complete_eps(self):
        catalog,book,priority=copy.deepcopy(self.inputs[:3])
        original=next(r for r in book['records'] if r.get('status')=='observed' and
            (r.get('annual') or {}).get('values',{}).get('owners_profit') is None)
        code=original['code']
        catalog['rows']=[r for r in catalog['rows'] if r['code']==code];catalog['listed_total']=1
        book['records']=[original];book['market_listed_total']=1
        priority['official_2025_owners_profit_missing_priority_codes']=[]
        priority['official_2025_owners_profit_missing_priority_count']=0
        # Isolate selection semantics; independent full report reconciliation
        # is tested with real inputs and a forged aggregate above.
        operational={'asof':catalog['date'],'source_digest':'a'*64,
            'rows':{code:{'parts':{a:{k:[0,0] for k in parts}
                for a,parts in completion.R1D_PART_LIMITS.items()}}}}
        with patch.object(completion,'r1d_public_reconciles',return_value={}):
            expanded,_,preferred,summary=completion.extend(catalog,book,priority,None,operational,{}, {})
        self.assertEqual(expanded['official_2025_owners_profit_missing_priority_codes'],[code])
        self.assertEqual(summary['pending_all_listed_issuers'],1)
        self.assertFalse(preferred)

    def test_six_cli_batches_checkpoint_without_requerying_or_exceeding_120(self):
        seen=[]
        def collect(catalog,book,priority,prior,**kwargs):
            day,_,expected,retained,selected=proof.plan(catalog,book,priority,prior,kwargs['limit'])
            for code in selected:
                self.assertNotIn(code,seen);seen.append(code)
                retained[code]=proof.parse_response({'status':'013'},corp='12345678',
                    base=expected[code],on_day=day)
                kwargs['on_progress'](proof.snapshot(day,catalog,book,priority,retained))
            return proof.snapshot(day,catalog,book,priority,retained),len(selected)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            fixture=copy.deepcopy(self.inputs)
            expanded,source,_,_=completion.extend(*fixture)
            _,_,expected,_,_=proof.plan(fixture[0],fixture[1],expanded,source)
            reopened=set(sorted(expected)[:120])
            source['records']={c:r for c,r in source['records'].items() if c not in reopened}
            source=proof.snapshot(source['market_asof'],fixture[0],fixture[1],expanded,source['records'])
            fixture[2]=expanded;fixture[3]=source
            for name,value in zip(NAMES,fixture):(root/name).write_text(json.dumps(value))
            out=io.StringIO()
            with patch.object(proof,'collect',side_effect=collect) as mocked, \
                 patch.object(proof,'_read_company_cache_from_root',return_value=None), \
                 patch.dict('sys.modules',{'requests':types.SimpleNamespace(Session=lambda:object())}), \
                 contextlib.redirect_stdout(out):
                proof.main(['--docs',str(root),'--mode','live','--completion-first','--batches','6'])
            self.assertEqual(mocked.call_count,6)
            self.assertEqual(len(seen),120)
            saved=json.loads((root/NAMES[3]).read_text())
            self.assertEqual(len(saved['records'])-len(fixture[3]['records']),120)
            self.assertTrue(all(saved['records'][c]==r for c,r in fixture[3]['records'].items()))
            self.assertEqual(json.loads(out.getvalue().splitlines()[-1])['newly_observed_profit_issuers'],0)
            self.assertTrue(proof.verify(saved,self.inputs[0],self.inputs[1],json.loads((root/NAMES[2]).read_text())))
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            proof.main(['--batches','7'])

    def test_earnings_yield_does_not_require_pbr_and_missing_stays_missing(self):
        from v3_dart_bulk_history import research_parts
        book=copy.deepcopy(self.inputs[1]);book['observations']=[]
        catalog=self.inputs[0]
        history=json.loads((ROOT/'v3-dart-bulk-history.json').read_text())
        valid=[r for r in book['records'] if r.get('status')=='observed' and
               (r.get('annual') or {}).get('values',{}).get('owners_profit') is not None][:3]
        self.assertEqual(len(valid),3)
        for row,value in zip(valid,(0,-123456789,None)):row['annual']['values']['owners_profit']=value
        result=research_parts(history,catalog,book,include_independent_earnings=True)
        rows={r['code']:r['unverified_components'] for r in result['rows']}
        for row,value in zip(valid[:2],(0,-123456789)):
            parts=rows[row['code']]
            self.assertEqual(parts['earnings_yield_pct']['max_points'],5)
            self.assertEqual(parts['earnings_yield_pct']['observed_value']==0,value==0)
            self.assertIsNone(parts['pbr']['candidate_points'])
        self.assertIsNone(rows[valid[2]['code']]['earnings_yield_pct']['candidate_points'])
        # The source-gated opt-in must not invalidate the existing saved
        # raw report contract or infer a profit when no account was observed.
        legacy=research_parts(history,catalog,book)
        self.assertEqual(legacy['companies_with_computable_earnings_yield_scores'],0)
        for row in valid[:2]:row['annual']['receipt_id']='20270101000001'
        guarded=research_parts(history,catalog,book,include_independent_earnings=True)
        guarded={r['code']:r['unverified_components'] for r in guarded['rows']}
        for row in valid[:2]:self.assertIsNone(guarded[row['code']]['earnings_yield_pct']['candidate_points'])
