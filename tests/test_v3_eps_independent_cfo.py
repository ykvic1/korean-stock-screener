import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_dart_xbrl_eps_recovery as r
import v3_dart_cashflow_enrichment as cf
from test_v3_dart_xbrl_eps_recovery import missing, xml, extract
from test_v3_dart_xbrl_capex_pilot import archive
from test_v3_dart_reported_eps import BASE, DAY, inputs
import v3_dart_reported_eps as eps


class IndependentEPSCFOTests(unittest.TestCase):
    def source(self):
        row=missing();code=row['code'];receipt=row['annual_receipt_id']
        bulk={'records':{code:{'years':{'2025':{'CFS':{'receipt_id':receipt}}}}}}
        cash={'records':{code:{'years':{'2025':{'scope':'OFS',
            'annual_receipt_id':receipt,'major_annual_receipt_matched':True,
            'operating_cashflow_krw':999}}}}}
        return row,bulk,cash

    def test_mismatched_scope_requires_independent_cfo_not_other_scope_amount(self):
        row,bulk,cash=self.source()
        choices=r.recovery_candidates({row['code']:row},cash,bulk)
        self.assertEqual(choices,[(row['code'],row,None)])
        anchor=cash['records'][row['code']]['years']['2025']
        anchor['scope']='CFS';anchor['operating_cashflow_krw']=0
        self.assertEqual(r.recovery_candidates({row['code']:row},cash,bulk)[0][2],0)
        anchor['operating_cashflow_krw']=True
        self.assertIsNone(r.recovery_candidates({row['code']:row},cash,bulk)[0][2])

    def test_fallback_cannot_borrow_other_scope_receipt_or_issuer(self):
        row,bulk,cash=self.source();code=row['code']
        for change in ('receipt','scope','identity'):
            b=copy.deepcopy(bulk);q=copy.deepcopy(row)
            if change=='receipt':b['records'][code]['years']['2025']['CFS']['receipt_id']='20260319000001'
            elif change=='scope':b['records'][code]['years']['2025']['OFS']=b['records'][code]['years']['2025'].pop('CFS')
            else:q['code']='999999'
            self.assertEqual(r.recovery_candidates({code:q},cash,b),[])

    def test_complete_and_same_receipt_reviewed_rows_not_requested_again(self):
        row,bulk,cash=self.source();code=row['code']
        reviewed=r.merge(row,{},reason='no_verified_cfo_scope',cfo_anchor=None,
                         archive_hash=None,profile_hash=None)
        self.assertEqual(r.recovery_candidates({code:reviewed},cash,bulk),[])
        done=r.merge(row,{'2023':-1.,'2024':0.,'2025':1.},reason='recovered',
                     cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)
        self.assertEqual(r.recovery_candidates({code:done},cash,bulk),[])

    def api(self):
        row,_,_=self.source()
        fact={'corp_code':'00123456','stock_code':row['code'],'bsns_year':'2025',
              'reprt_code':'11011','sj_div':'CF','account_detail':'-', 'fs_div':'CFS',
              'rcept_no':row['annual_receipt_id'],'currency':'KRW',
              'account_id':'ifrs-full_CashFlowsFromUsedInOperatingActivities','thstrm_amount':'100'}
        args=dict(code=row['code'],corp='00123456',scope='CFS',receipt=row['annual_receipt_id'],day=DAY)
        return fact,args

    def test_independent_api_normalizes_identity_scope_receipt_and_explicit_zero(self):
        fact,args=self.api()
        for amount in ('100','0','-100'):
            value,digest=r.official_cfo_anchor({'status':'000','list':[{**fact,'thstrm_amount':amount}]},**args)
            self.assertEqual(value,int(amount));self.assertEqual(len(digest),64)
        for key,value in [('fs_div','OFS'),('currency','USD'),('corp_code','00999999')]:
            with self.assertRaises(cf.CashflowError):r.official_cfo_anchor({'status':'000','list':[{**fact,key:value}]},**args)
        self.assertEqual(r.official_cfo_anchor({'status':'000','list':[{**fact,'rcept_no':'20260319000001'}]},**args),(None,None))

    def test_api_absence_not_zero_and_provider_errors_not_negative_reviews(self):
        _,args=self.api()
        self.assertEqual(r.official_cfo_anchor({'status':'013'},**args),(None,None))
        for payload in ({'status':'020'},{'status':'010'},{'status':'000','list':[]},None):
            with self.assertRaises(r.OriginalEPSError):r.official_cfo_anchor(payload,**args)

    def test_api_proof_is_bound_to_eps_and_legacy_proofs_still_replay(self):
        fact,args=self.api();value,digest=r.official_cfo_anchor({'status':'000','list':[fact]},**args)
        row=r.merge(missing(),{'2023':-1.,'2024':0.,'2025':1.},reason='recovered',
            cfo_anchor=value,archive_hash='a'*64,profile_hash='b'*64,cfo_response_hash=digest)
        self.assertTrue(r.verify_review(row));c,b=inputs()
        state=eps.snapshot(DAY,c,b,{BASE['code']:BASE},{BASE['code']:row});eps.plan(c,b,state)
        self.assertEqual(state['complete_three_year_eps_issuers'],1)
        bad=copy.deepcopy(row);bad['original_xbrl_proof']['cfo_response_sha256']='invalid'
        bad['original_xbrl_proof']['digest']=cf.fingerprint({k:v for k,v in bad['original_xbrl_proof'].items() if k!='digest'})
        with self.assertRaises(r.OriginalEPSError):r.verify_review(bad)
        legacy=copy.deepcopy(row);legacy['original_xbrl_proof'].pop('cfo_response_sha256')
        legacy['original_xbrl_proof']['digest']=cf.fingerprint({k:v for k,v in legacy['original_xbrl_proof'].items() if k!='digest'})
        self.assertTrue(r.verify_review(legacy))

    def test_extension_research_does_not_block_standard_eps_archive_safety_stays(self):
        with patch.object(r.xp,'_extension_label_evidence',side_effect=r.xp.XbrlPilotError('ambiguous_local_presentation_locator')):
            self.assertEqual(extract(archive(xml()))[0]['2025'],150.25)
        dangerous=xml().replace(b'<xbrl',b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><xbrl',1)
        with self.assertRaises(r.xp.XbrlPilotError):extract(archive(dangerous))

    def test_eps_writers_queue_and_backup_does_not_alter_scoring(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('v3-xbrl-eps-recovery.yml','v3-dart-reported-eps.yml'):
            text=(root/'.github/workflows'/name).read_text()
            self.assertIn('group: v3-dart-reported-basic-eps',text)
            self.assertIn('queue: max',text)
        text=(root/'.github/workflows/v3-xbrl-eps-recovery.yml').read_text()
        self.assertIn("cron: '07 11 * * 1-5'",text)
        self.assertLess(text.index('Verify and publish derived observations'),text.index('Continue finite original EPS'))


if __name__=='__main__':unittest.main()
