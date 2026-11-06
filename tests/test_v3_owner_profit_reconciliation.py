import copy
import unittest
import contextlib
import io
import json
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import v3_dart_2025_owner_profit_probe as p
import v3_owner_profit_reconciliation as allocation
from v3_dart_bulk_history import make_company_mapping_cache
import test_v3_dart_2025_owner_profit_probe as fixtures


class ProfitAllocationTests(unittest.TestCase):
    def inputs(self):
        catalog,book,priority=fixtures.inputs()
        day,members,expected,_,_=p.plan(catalog,book,priority)
        return catalog,book,priority,day,members,expected

    def rows(self,base,total='100',nci='20'):
        row=fixtures.OwnerProfitSourceTests().row(base,total)
        row['sj_div']='IS'
        return [{**row,'account_id':sorted(allocation.TOTAL_IDS)[0]},
                {**row,'account_id':sorted(allocation.NCI_IDS)[0],'thstrm_amount':nci}]

    def parse(self,rows,base,day,enabled=True):
        return p.parse_response({'status':'000','list':rows},corp='12345678',
            base=base,on_day=day,allow_reconciliation=enabled)

    def test_exact_profit_allocation_observed_zero_and_loss_are_not_missing(self):
        *_,day,members,expected=self.inputs();base=expected['000001']
        for total,nci in ((100,20),(0,0),(-100,20),(100,-20)):
            with self.subTest(total=total,nci=nci):
                rows=self.rows(base,str(total),str(nci))
                old=self.parse(rows,base,day,False)
                self.assertIsNone(old['owners_profit_krw'])
                result=self.parse(rows,base,day)
                self.assertEqual(result['owners_profit_krw'],total-nci)
                self.assertEqual(result['source_account_id'],allocation.DERIVED_ID)
                self.assertTrue(allocation.verify(result['profit_reconciliation'],total-nci))
                self.assertFalse(result['raw_provider_rows_published'])

    def test_missing_nci_or_total_is_never_assumed_zero(self):
        *_,day,members,expected=self.inputs();base=expected['000001']
        source=self.rows(base)
        for rows in (source[:1],source[1:],
                     [{**r,'account_id':r['account_id'].replace('ProfitLoss','ComprehensiveIncome')} for r in source],
                     [source[0],{**source[1],'account_id':'ifrs-full_NoncontrollingInterests'}]):
            result=self.parse(rows,base,day)
            self.assertEqual(result['status'],'missing_standard_account')
            self.assertIsNone(result['owners_profit_krw'])

    def test_statement_detail_year_currency_and_receipt_never_mix(self):
        *_,day,members,expected=self.inputs();base=expected['000001']
        rows=self.rows(base);rows[1]['sj_div']='CIS'
        self.assertIsNone(self.parse(rows,base,day)['owners_profit_krw'])
        rows=self.rows(base);rows[1]['account_detail']='사업부별 세부항목'
        self.assertIsNone(self.parse(rows,base,day)['owners_profit_krw'])
        for key,value in (('currency','USD'),('rcept_no','20260321000001'),
                          ('bsns_year','2024'),('fs_div','OFS'),('corp_code','87654321')):
            rows=self.rows(base);rows[1][key]=value
            with self.subTest(key=key),self.assertRaises(p.ProfitProbeError):self.parse(rows,base,day)

    def test_ambiguous_standard_values_and_out_of_bound_difference_fail(self):
        *_,day,members,expected=self.inputs();base=expected['000001']
        rows=self.rows(base)
        cases=[rows+[{**rows[0],'thstrm_amount':'999'}],
               rows+[{**rows[1],'thstrm_amount':'999'}],
               rows+[{**r,'sj_div':'CIS','thstrm_amount':'999'} for r in rows],
               self.rows(base,str(10**18),str(-10**18))]
        for sample in cases:
            with self.assertRaises(p.ProfitProbeError):self.parse(sample,base,day)

    def test_direct_reported_parent_profit_is_preserved_and_malformed_is_not_bypassed(self):
        *_,day,members,expected=self.inputs();base=expected['000001']
        direct=fixtures.OwnerProfitSourceTests().row(base,'70')
        result=self.parse(self.rows(base)+[direct],base,day)
        self.assertEqual(result['owners_profit_krw'],70)
        self.assertNotIn('profit_reconciliation',result)
        direct['thstrm_amount']='not-an-integer'
        with self.assertRaises(p.ProfitProbeError):self.parse(self.rows(base)+[direct],base,day)

    def test_derived_provenance_is_replayed_and_tampering_is_rejected(self):
        catalog,book,priority,day,_,expected=self.inputs();base=expected['000001']
        result=self.parse(self.rows(base),base,day)
        source=p.snapshot(day,catalog,book,priority,{base['code']:result})
        self.assertTrue(p.verify(source,catalog,book,priority))
        for mutate in (
            lambda r:r.update(owners_profit_krw=81),
            lambda r:r['profit_reconciliation']['total_profit'].update(krw=True),
            lambda r:r['profit_reconciliation']['noncontrolling_profit'].update(account_id='ifrs-full_NoncontrollingInterests'),
            lambda r:r['profit_reconciliation'].update(statement='BS'),
            lambda r:r.update(source_account_id=sorted(p.PROFIT_IDS['CFS'])[0]),
        ):
            changed=copy.deepcopy(source);mutate(changed['records'][base['code']])
            with self.assertRaises(p.ProfitProbeError):p.verify(changed,catalog,book,priority)

    def test_recheck_only_missing_receipts_once_and_prioritize_full_completion(self):
        catalog,book,priority,day,members,expected=self.inputs()
        records={code:p.parse_response({'status':'013'},corp='12345678',base=base,on_day=day)
                 for code,base in expected.items()}
        old=p.snapshot(day,catalog,book,priority,records)
        mapping={code:str(12345678+i) for i,code in enumerate(expected)}
        reverse={v:k for k,v in mapping.items()};calls=[]
        parent=self
        class Provider:
            def get(self,url,params,timeout):
                code=reverse[params['corp_code']];calls.append(code)
                rows=parent.rows(expected[code])
                for row in rows:row['corp_code']=params['corp_code']
                return fixtures.Result({'status':'000','list':rows})
        cache=make_company_mapping_cache(mapping,day,members)
        first,n=p.collect(catalog,book,priority,old,key='synthetic-test-not-credential',
            session=Provider(),cache=cache,limit=1,preferred_codes={'000002'},recover_reconciliation=True)
        self.assertEqual((n,calls),(1,['000002']))
        self.assertEqual(first['records']['000001'],records['000001'])
        second,n=p.collect(catalog,book,priority,first,key='synthetic-test-not-credential',
            session=Provider(),cache=cache,recover_reconciliation=True)
        self.assertEqual((n,calls),(1,['000002','000001']))
        self.assertEqual(second['records']['000002'],first['records']['000002'])
        self.assertEqual(second['reconciliation_derived_issuers'],2)
        third,n=p.collect(catalog,book,priority,second,key=None,session=Provider(),recover_reconciliation=True)
        self.assertEqual(n,0);self.assertEqual(third,second)
        changed=copy.deepcopy(book);changed['records'][0]['annual']['receipt_id']='20260322000001'
        _,_,new_expected,retained,_=p.plan(catalog,changed,priority,third)
        self.assertEqual(allocation.pending(new_expected,retained),{'000001'})

    def test_negative_recheck_is_checkpointed_without_false_observation_or_repetition(self):
        catalog,book,priority,day,members,expected=self.inputs()
        records={code:p.parse_response({'status':'013'},corp='12345678',base=base,on_day=day)
                 for code,base in expected.items()}
        old=p.snapshot(day,catalog,book,priority,records)
        cache=make_company_mapping_cache({c:str(12345678+i) for i,c in enumerate(expected)},day,members)
        provider=fixtures.Source({'status':'013'})
        saved=[]
        result,n=p.collect(catalog,book,priority,old,key='synthetic-test-not-credential',
            session=provider,cache=cache,recover_reconciliation=True,on_progress=saved.append)
        self.assertEqual((n,len(saved)),(2,2))
        self.assertEqual(result['observed_official_owner_profit_issuers'],0)
        self.assertEqual(result['reconciliation_rechecked_issuers'],2)
        self.assertTrue(all(r['owners_profit_krw'] is None for r in result['records'].values()))
        again,n=p.collect(catalog,book,priority,result,key=None,session=provider,recover_reconciliation=True)
        self.assertEqual(n,0);self.assertEqual(again,result)

    def test_cli_continues_rechecks_when_the_original_queue_was_already_fully_queried(self):
        catalog,book,priority,day,members,expected=self.inputs()
        records={code:p.parse_response({'status':'013'},corp='12345678',base=base,on_day=day)
                 for code,base in expected.items()}
        old=p.snapshot(day,catalog,book,priority,records)
        mapping={code:str(12345678+i) for i,code in enumerate(expected)}
        reverse={v:k for k,v in mapping.items()};calls=[];parent=self
        class Provider:
            def get(self,url,params,timeout):
                code=reverse[params['corp_code']];calls.append(code)
                rows=parent.rows(expected[code])
                for row in rows:row['corp_code']=params['corp_code']
                return fixtures.Result({'status':'000','list':rows})
        names=('v22-market-catalog.json','v3-dart-book-reference.json',
               'v3-near-100-evidence-priority.json','v3-dart-owner-profit-supplement.json')
        cache=make_company_mapping_cache(mapping,day,members)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in zip(names,(catalog,book,priority,old)):
                (root/name).write_text(json.dumps(value))
            output=io.StringIO()
            with patch.object(p,'_read_company_cache_from_root',return_value=cache), \
                 patch.dict('sys.modules',{'requests':types.SimpleNamespace(Session=Provider)}), \
                 patch.dict('os.environ',{'DART_API_KEY':'synthetic-test-not-credential'}), \
                 contextlib.redirect_stdout(output):
                p.main(['--docs',str(root),'--mode','live','--limit','1','--batches','6','--recover-reconciliation'])
            self.assertEqual(calls,['000001','000002'])
            result=json.loads((root/names[-1]).read_text())
            self.assertEqual(result['reconciliation_derived_issuers'],2)
            summary=json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(summary['reconciliation_pending_issuers'],0)

    def test_quota_failure_does_not_mark_a_missing_receipt_rechecked(self):
        catalog,book,priority,day,members,expected=self.inputs()
        records={code:p.parse_response({'status':'013'},corp='12345678',base=base,on_day=day)
                 for code,base in expected.items()}
        old=p.snapshot(day,catalog,book,priority,records);before=copy.deepcopy(old)
        cache=make_company_mapping_cache({c:str(12345678+i) for i,c in enumerate(expected)},day,members)
        saved=[]
        with self.assertRaisesRegex(p.ProfitProbeError,'official_profit_provider_status_unavailable'):
            p.collect(catalog,book,priority,old,key='synthetic-test-not-credential',
                session=fixtures.Source({'status':'020'}),cache=cache,
                recover_reconciliation=True,on_progress=saved.append)
        self.assertFalse(saved);self.assertEqual(old,before)
