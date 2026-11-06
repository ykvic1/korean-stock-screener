import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
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
        self.assertEqual(completion.extend(*lag)[3]['status'],'waiting_current_eps_receipts')

    def simulated_source(self):
        catalog,book,old_priority,prior,*_=self.inputs
        priority,rebound,preferred,summary=completion.extend(*self.inputs)
        day,members,expected,retained,_=proof.plan(catalog,book,priority,rebound)
        mapping={code:f'{10000000+i:08d}' for i,code in enumerate(sorted(members))}
        reverse={v:k for k,v in mapping.items()}
        pending=preferred-set(retained)
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
            for code in pending:
                self.assertAlmostEqual(report['rows'][code]['available'],100)
            before=sum(r['available']==100 for r in self.inputs[4]['rows'].values())
            self.assertEqual(sum(r['available']==100 for r in report['rows'].values()),before+len(pending))
            self.assertEqual(json.loads((root/NAMES[2]).read_text()),priority)
            self.assertFalse(report['investment_score_certified'])
