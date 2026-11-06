"""One-time literal Korean EPS account rechecks; no real network or key."""
import copy
import unittest
from unittest.mock import patch

import v3_dart_reported_eps as eps
from v3_operational_research_score import verified_reported_eps
from test_v3_dart_reported_eps import BASE, DAY, inputs, payload


def fallback(label='기본주당이익', amount='350'):
    row=copy.deepcopy(payload()['list'][0])
    row.update(account_id=eps.NONSTANDARD_ID, account_nm=label,
               bfefrmtrm_amount='100', frmtrm_amount='200',
               thstrm_amount=amount)
    return row


def blank_state():
    catalog,book=inputs()
    missing=eps.parse({'status':'013'},corp='00123456',base=BASE,on_day=DAY)
    source=eps.snapshot(DAY,catalog,book,{BASE['code']:BASE},{BASE['code']:missing})
    return catalog,book,source


class FakeResponse:
    def __init__(self, body):self.body=body
    def raise_for_status(self):pass
    def json(self):return self.body


class FakeSession:
    def __init__(self, body):self.body=body;self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append(kwargs['params'])
        return FakeResponse(self.body)


class EPSNameRecheckTests(unittest.TestCase):
    def test_literal_basic_eps_name_recovers_complete_reported_comparatives_once(self):
        catalog,book,prior=blank_state()
        source={'status':'000','list':[fallback()]}
        session=FakeSession(source)
        with patch.object(eps,'verified_company_mapping_cache',return_value={'005930':'00123456'}):
            out,calls=eps.collect(catalog,book,prior,key='a'*40,session=session,
                recover_missing_names=True,priority_codes={'005930'})
            replay,reused=eps.collect(catalog,book,out,key='a'*40,session=FakeSession(source),
                recover_missing_names=True,priority_codes={'005930'})
        self.assertEqual(calls,1)
        self.assertEqual(reused,0)
        self.assertEqual(out,replay)
        self.assertEqual(out['complete_three_year_eps_issuers'],1)
        self.assertEqual(out['name_recheck_issuers'],1)
        self.assertEqual(out['name_recheck_enriched_issuers'],1)
        self.assertEqual(out['name_recheck_complete_three_year_issuers'],1)
        row=out['records']['005930']
        self.assertEqual(row['source_account_id'],eps.NONSTANDARD_ID)
        self.assertEqual(row['source_account_name_fallback'],'기본주당이익')
        self.assertEqual(row['reported_basic_eps_2023_2025'],[100,200,350])
        self.assertIn('005930',verified_reported_eps(catalog,book,out))
        self.assertEqual(eps.plan(catalog,book,out,recover_missing_names=True)[-1],[])

    def test_exact_name_not_accepted_in_initial_standard_id_collection(self):
        nonstandard={'status':'000','list':[fallback()]}
        unverified=eps.parse(nonstandard,corp='00123456',base=BASE,on_day=DAY)
        self.assertEqual(unverified['status'],'standard_eps_not_observed')
        verified=eps.parse(nonstandard,corp='00123456',base=BASE,on_day=DAY,
                           allow_nonstandard_names=True)
        self.assertEqual(verified['status'],'reported_basic_eps')
        self.assertEqual(verified['reported_basic_eps_2023_2025'],[100,200,350])

    def test_untrusted_or_ambiguous_labels_never_create_eps(self):
        for row in (fallback(label='기본주당 이익'),
                    fallback(label='희석주당이익'),
                    fallback(label='당기순이익'),
                    dict(fallback(),account_id='dart_CustomBasicEPS')):
            with self.subTest(row=row):
                got=eps.parse({'status':'000','list':[row]},
                    corp='00123456',base=BASE,on_day=DAY,
                    allow_nonstandard_names=True)
                self.assertEqual(got['reported_basic_eps_2023_2025'],[None]*3)
        with self.assertRaisesRegex(eps.EPSError,'conflicting_reported_basic_eps'):
            eps.parse({'status':'000','list':[fallback(),fallback(amount='351')]},
                corp='00123456',base=BASE,on_day=DAY,allow_nonstandard_names=True)

    def test_exact_name_same_receipt_scope_currency_and_identity_still_required(self):
        for overrides in (
            {'rcept_no':'20260319000001'}, {'currency':'USD'},
            {'fs_div':'OFS'}, {'sj_div':'CF'},
        ):
            row=dict(fallback(),**overrides)
            if overrides=={'sj_div':'CF'}:
                got=eps.parse({'status':'000','list':[row]},corp='00123456',base=BASE,
                    on_day=DAY,allow_nonstandard_names=True)
                self.assertEqual(got['status'],'standard_eps_not_observed')
            elif 'fs_div' in overrides:
                with self.assertRaisesRegex(eps.EPSError,'identity_mismatch'):
                    eps.parse({'status':'000','list':[row]},corp='00123456',base=BASE,
                        on_day=DAY,allow_nonstandard_names=True)
            else:
                with self.assertRaisesRegex(eps.EPSError,'receipt_or_currency_mismatch'):
                    eps.parse({'status':'000','list':[row]},corp='00123456',base=BASE,
                        on_day=DAY,allow_nonstandard_names=True)

    def test_observed_zero_and_negative_eps_are_not_missing(self):
        for value in ('0','-100','(100)'):
            got=eps.parse({'status':'000','list':[fallback(amount=value)]},
                corp='00123456',base=BASE,on_day=DAY,
                allow_nonstandard_names=True)
            self.assertEqual(got['reported_basic_eps_2023_2025'][-1],
                             0 if value=='0' else -100)
            self.assertTrue(got['metrics']['complete_three_years'])

    def test_no_match_is_marked_checked_without_zero_or_provider_repeat(self):
        catalog,book,prior=blank_state()
        session=FakeSession({'status':'013'})
        with patch.object(eps,'verified_company_mapping_cache',return_value={'005930':'00123456'}):
            out,calls=eps.collect(catalog,book,prior,key='a'*40,session=session,
                recover_missing_names=True)
        self.assertEqual(calls,1)
        self.assertEqual(out['complete_three_year_eps_issuers'],0)
        self.assertEqual(out['name_recheck_issuers'],1)
        self.assertEqual(out['name_recheck_enriched_issuers'],0)
        self.assertEqual(out['records']['005930']['source_name_recheck']['result'],
                         'checked_unavailable')
        self.assertEqual(eps.plan(catalog,book,out,recover_missing_names=True)[-1],[])

    def test_marker_and_provenance_are_validated_against_original_receipt(self):
        catalog,book,prior=blank_state()
        source={'status':'000','list':[fallback()]}
        with patch.object(eps,'verified_company_mapping_cache',return_value={'005930':'00123456'}):
            out,_=eps.collect(catalog,book,prior,key='a'*40,session=FakeSession(source),
                recover_missing_names=True)
        for key,value in (('receipt_id','20260319000001'),('result','checked_unavailable'),
                          ('version','invented')):
            damaged=copy.deepcopy(out)
            damaged['records']['005930']['source_name_recheck'][key]=value
            with self.subTest(key=key),self.assertRaises(eps.EPSError):
                eps.plan(catalog,book,damaged,recover_missing_names=True)
        damaged=copy.deepcopy(out)
        damaged['records']['005930']['source_account_name_fallback']='희석주당이익'
        with self.assertRaises(eps.EPSError):
            eps.plan(catalog,book,damaged,recover_missing_names=True)

    def test_recovery_prioritizes_actual_missing_eps_and_rejects_unknown_codes(self):
        catalog,book,prior=blank_state()
        for code in ('000001','000002','000003'):
            catalog['rows'].append({'code':code,'name':code,'market':'KOSDAQ'})
            book['records'].append({'code':code,'status':'observed','annual':{
                'year':2025,'fs_div':'CFS','currency':'KRW',
                'receipt_id':BASE['annual_receipt_id']}})
            prior['records'][code]={**prior['records']['005930'],
                                    'code':code,'name':code,'market':'KOSDAQ'}
        catalog['listed_total']=4
        # Rebuild source fingerprints and identity before calculating priority.
        prior=eps.snapshot(DAY,catalog,book,{c:r for c,r in
             [(x['code'], {**BASE,'code':x['code'],'name':x['name'],
                         'market':x['market']}) for x in catalog['rows']]},
             prior['records'])
        chosen=eps.plan(catalog,book,prior,limit=2,
            recover_missing_names=True,priority_codes={'000003'})[-1]
        self.assertEqual(chosen[0],'000003')
        with self.assertRaises(eps.EPSError):
            eps.plan(catalog,book,prior,limit=2,recover_missing_names=True,
                     priority_codes={'999999'})


if __name__=='__main__':unittest.main()
