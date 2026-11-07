import copy
import unittest
import signal
import v3_dart_full_intangible_total as t
import v3_dart_cashflow_enrichment as cf
import v3_dart_cashflow_factor_preview as factor
from test_v3_dart_reported_capex_total import prior,DAY,CODE,CORP
from test_v3_dart_cashflow_enrichment import rows,live_inputs


@unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'Linux live collector deadline')
class ProviderWallDeadlineTests(unittest.TestCase):
    def test_total_deadline_interrupts_an_active_response_and_restores_timer(self):
        previous = signal.getsignal(signal.SIGALRM)
        class Client:
            calls = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                signal.raise_signal(signal.SIGALRM)
        client = Client()
        with self.assertRaisesRegex(t.IntangibleTotalError, '^full_intangible_provider_deadline$'):
            t.bounded_provider_response(client,'fnlttSinglAcntAll.json',{'crtfc_key':'secret'},remaining=75)
        self.assertEqual(client.calls, 1)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0,0.0))

    def test_existing_timer_is_not_overwritten(self):
        signal.setitimer(signal.ITIMER_REAL, 30)
        try:
            with self.assertRaisesRegex(t.IntangibleTotalError, 'wall_deadline_unavailable'):
                with t.request_wall_deadline(75): self.fail('must not enter')
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 25)
        finally:signal.setitimer(signal.ITIMER_REAL, 0)

    def test_exhausted_batch_budget_does_not_make_a_request(self):
        class Client:
            def get(self, *args, **kwargs): raise AssertionError('no request authorized')
        with self.assertRaisesRegex(t.IntangibleTotalError, '^full_intangible_provider_deadline$'):
            t.bounded_provider_response(Client(),'x',{},remaining=11)

    def test_transport_errors_never_expose_request_credentials(self):
        class Client:
            def get(self, *args, **kwargs): raise ValueError('secret URL and crtfc_key')
        with self.assertRaisesRegex(t.IntangibleTotalError, '^full_intangible_provider_unavailable$'):
            t.bounded_provider_response(Client(),'x',{},remaining=75)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0,0.0))

    def test_loaded_response_is_closed_and_oversized_bodies_rejected(self):
        class Large:
            def __len__(self):return 25_000_001
        class Response:
            closed = False
            content = b'{}'
            def raise_for_status(self):pass
            def close(self):self.closed=True
        class Client:
            def __init__(self,response):self.response=response
            def get(self,*args,**kwargs):return self.response
        response=Response()
        self.assertIs(t.bounded_provider_response(Client(response),'x',{},remaining=75),response)
        self.assertTrue(response.closed)
        response=Response();response.content=Large()
        with self.assertRaises(t.IntangibleTotalError):
            t.bounded_provider_response(Client(response),'x',{},remaining=75)
        self.assertTrue(response.closed)


def tax_payloads():
    return {s:{'status':'000','list':[{'sj_div':s,'account_id':t.ACCOUNT,
        'account_nm':'영업권 이외의 무형자산의 취득','label_kor':'영업권 이외의 무형자산의 취득',
        'label_eng':'Purchase of intangible assets other than goodwill','data_tp':'(X)','bsns_de':'20180101'}]} for s in ('CF1','CF2','CF3','CF4')}


def payload(old,value='100000000',name='영업권 이외의 무형자산의 취득'):
    base=rows(old['year'],CODE,skip_intangible=True)
    return {'status':'000','list':base+[{**base[0],'account_id':t.ACCOUNT,'account_nm':name,'thstrm_amount':value}]}


def recover(old,body=None,cert=None):
    normalized,proof=t.parse(body or payload(old),old,code=CODE,corp=CORP,day=DAY,cert=cert or t.certificate(tax_payloads()))
    return t.merge(old,normalized,proof)


class FullIntangibleTests(unittest.TestCase):
    def test_official_scope_sections_match_consolidated_1_3_and_separate_2_4(self):
        cert=t.certificate(tax_payloads());self.assertTrue(t.verify_certificate(cert,'CFS'));self.assertTrue(t.verify_certificate(cert,'OFS'))
        parts=tax_payloads();parts['CF2']['list']=[];parts['CF4']['list']=[]
        cert=t.certificate(parts);self.assertTrue(t.verify_certificate(cert,'CFS'))
        with self.assertRaises(t.IntangibleTotalError):t.verify_certificate(cert,'OFS')

    def test_official_definition_must_be_whole_intangible_monetary_cashflow(self):
        for changes in ({'sj_div':'BS1'},{'data_tp':'Shares'}, {'account_nm':'소프트웨어의취득','label_kor':'소프트웨어의취득','label_eng':'Purchase of software'}):
            data=tax_payloads();data['CF1']['list'][0].update(changes)
            with self.assertRaises(t.IntangibleTotalError):t.certificate(data)
        data=tax_payloads();data['CF1']['status']='020'
        with self.assertRaises(t.IntangibleTotalError):t.certificate(data)
        data=tax_payloads();data.pop('CF4')
        with self.assertRaises(t.IntangibleTotalError):t.certificate(data)

    def test_reported_parent_total_is_used_once_not_added_to_software_child(self):
        old=prior(ppe=300000000);body=payload(old)
        body['list'].append({**body['list'][0],'account_id':'dart_PurchaseOfComputerSoftware','account_nm':'컴퓨터소프트웨어의취득','thstrm_amount':'50000000'})
        out=recover(old,body)
        self.assertEqual(out['intangible_capex_krw'],100000000);self.assertEqual(out['total_capex_krw'],400000000);self.assertEqual(out['free_cashflow_krw'],800000000)
        self.assertTrue(t.verify(out));self.assertIsNone(old['intangible_capex_krw'])
        state=cf.summary(DAY,{CODE:{}},{CODE:{'scope':'CFS','years':{'2025':out}}},'a'*64)
        self.assertEqual(state['metrics']['official_full_intangible_total_company_years'],1)
        self.assertEqual(state['metrics']['complete_capex_pair_company_years'],1)
        self.assertFalse(state['historical_PIT_certified'])

    def test_child_only_or_narrower_label_never_certifies_total(self):
        old=prior(ppe=300000000);body=payload(old)
        body['list'][-1].update(account_id='dart_PurchaseOfComputerSoftware',account_nm='소프트웨어의취득')
        out=recover(old,body);self.assertIsNone(out['intangible_capex_krw']);self.assertIsNone(out['free_cashflow_krw'])
        with self.assertRaises(t.IntangibleTotalError):recover(old,payload(old,name='소프트웨어의취득'))

    def test_wrong_scope_company_receipt_currency_and_period_reject(self):
        old=prior(ppe=300000000)
        for key,value in (('corp_code','99999999'),('stock_code','999999'),('bsns_year','2024'),('fs_div','OFS'),('rcept_no','20260321000001'),('currency','USD')):
            body=payload(old);body['list'][-1][key]=value
            with self.assertRaises((t.IntangibleTotalError,cf.CashflowError)):recover(old,body)

    def test_blank_negative_or_conflicting_totals_never_become_zero(self):
        old=prior(ppe=300000000)
        for value in ('','-','-100','100.1','NaN'):
            with self.assertRaises(t.IntangibleTotalError):recover(old,payload(old,value=value))
        zero=recover(old,payload(old,value='0'));self.assertEqual(zero['intangible_capex_krw'],0);self.assertEqual(zero['free_cashflow_krw'],900000000)
        body=payload(old);body['list'].append({**body['list'][-1],'thstrm_amount':'100000001'})
        with self.assertRaises(t.IntangibleTotalError):recover(old,body)

    def test_cfo_or_known_ppe_cannot_be_overwritten(self):
        old=prior(ppe=300000000);body=payload(old);body['list'][0]['thstrm_amount']='1200000001'
        out=recover(old,body);self.assertEqual(out['full_intangible_review']['reason'],'unverified_anchor');self.assertIsNone(out['free_cashflow_krw'])
        body=payload(old);body['list'][1]['thstrm_amount']='300000001'
        out=recover(old,body);self.assertEqual(out['full_intangible_review']['reason'],'known_amount_conflict');self.assertEqual(out['ppe_capex_krw'],300000000)
        out=recover(old)
        with self.assertRaises(t.IntangibleTotalError):recover(out)

    def test_a_different_supported_total_is_not_silently_replaced(self):
        old=prior(ppe=300000000);body=payload(old)
        body['list'].append({**body['list'][-1],'account_id':'ifrs-full_PurchaseOfIntangibleAssets','thstrm_amount':'99999999'})
        out=recover(old,body);self.assertEqual(out['full_intangible_review']['reason'],'known_amount_conflict');self.assertIsNone(out['intangible_capex_krw'])

    def test_source_or_taxonomy_proof_tampering_fails_ledger_validation(self):
        out=recover(prior(ppe=300000000))
        for key,value in (('scope','OFS'),('intangible_capex_krw',1),('annual_receipt_id','20260321000001')):
            bad=copy.deepcopy(out);bad[key]=value
            with self.assertRaises(t.IntangibleTotalError):t.verify(bad)
            with self.assertRaises(cf.CashflowError):cf.summary(DAY,{CODE:{}},{CODE:{'scope':bad['scope'],'years':{'2025':bad}}},'a'*64)
        bad=copy.deepcopy(out);bad['full_intangible_total']['taxonomy']['sections']['CF1']['label']='소프트웨어의취득'
        with self.assertRaises(t.IntangibleTotalError):t.verify(bad)

    def test_three_year_factors_replay_only_actual_parent_receipts(self):
        market,catalog,bulk=live_inputs();records={}
        for y in (2023,2024,2025):
            records[str(y)]=recover(prior(y,ppe=300000000));bulk['records'][CODE]['years'][str(y)]['CFS']['values']['assets']=10000000000
        state=cf.summary(catalog['date'],{r['code']:r for r in catalog['rows']},{CODE:{'scope':'CFS','years':records}},cf.fingerprint(bulk))
        cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True)
        self.assertEqual(state['metrics']['three_year_fcf_receipt_matched_issuers'],1)
        self.assertEqual(factor.real_features(catalog,bulk,state)[CODE]['financial_fcf_assets_3y_pct'],8.0)

    def test_rejected_reviews_are_not_requeried_and_completion_issuers_go_first(self):
        old=prior(ppe=300000000);checked=t.merge(old,None,None)
        state={'records':{'000001':{'years':{'2025':prior()}},'000002':{'years':{'2025':old}}}}
        self.assertEqual(t.targets(state,{'000001','000002'},1)[0][0],'000002')
        state['records']['000002']['years']['2025']=checked
        self.assertTrue(all(code!='000002' for code,_,_ in t.targets(state,{'000001','000002'},8)))
