import copy
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import v3_dart_reported_capex_total as t
import v3_dart_cashflow_enrichment as cf
import v3_dart_cashflow_factor_preview as factor
from v3_dart_cashflow_gap_audit import _classify
from test_v3_dart_cashflow_enrichment import live_inputs,rows
from test_v3_dart_bulk_history import CODES,CORPS

DAY='2026-11-06'
CODE=CODES[0]
CORP=CORPS[CODE]

def prior(year=2025,ppe=None,intangible=None):
    return {'year':year,'scope':'CFS','annual_receipt_id':f'{year+1}0318000001','major_annual_receipt_matched':True,
            'operating_cashflow_krw':1200000000,'ppe_capex_krw':ppe,'intangible_capex_krw':intangible,
            'total_capex_krw':None,'free_cashflow_krw':None,'raw_source_account_rows_published':False,'historical_PIT_certified':False}


def payload(old,label='유형자산 및 무형자산의 취득',amount='400000000',**changes):
    cfo=rows(old['year'],CODE)[0]
    total={**cfo,'account_id':cf.NONSTANDARD_ACCOUNT_ID,'account_nm':label,'thstrm_amount':amount,**changes}
    return {'status':'000','list':[cfo,total]}


def recover(old,body=None):
    normalized,proof=t.parse(body or payload(old),old,code=CODE,corp=CORP,day=DAY)
    return t.merge(old,normalized,proof)


class ReportedTotalTests(unittest.TestCase):
    def test_verified_reported_total_workflow_has_bounded_next_day_source_cadence(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        text=(root/'.github/workflows/v3-reported-capex-total.yml').read_text()
        self.assertIn("cron: '20 22 * * 1-5'",text)
        self.assertIn("github.event_name == 'pull_request' || github.event_name == 'push'",text)
        self.assertIn("needs.offline.result == 'skipped'",text)
        self.assertIn("--mode live --limit 40 --focus",text)
        self.assertIn("inputs.focus || 'all'",text)
        manual=(root/'.github/workflows/v3-manual-90first-source-burst.yml').read_text()
        self.assertIn("inputs[focus]=sub90",manual)
        self.assertIn("group: korea-stock-screener-dart-cashflow",text)
        self.assertIn("v3-current-observed-scorecard.yml/dispatches",text)
        self.assertNotIn("--recheck-equivalent-contexts",text)


    def test_reported_combined_total_computes_fcf_without_inventing_components(self):
        old=prior();out=recover(old)
        self.assertEqual(out['total_capex_krw'],400000000);self.assertEqual(out['free_cashflow_krw'],800000000)
        for k in ('ppe_capex_krw','intangible_capex_krw'):self.assertIsNone(out[k]);self.assertIsNone(old[k])
        self.assertEqual(t.verify(out),400000000)
        s=cf.summary(DAY,{CODE:{}},{CODE:{'scope':'CFS','years':{'2025':out}}},'a'*64)
        self.assertEqual(s['metrics']['fcf_company_years'],1)
        self.assertEqual(s['metrics']['complete_capex_pair_company_years'],0)
        self.assertEqual(s['metrics']['reported_combined_capex_company_years'],1)
        self.assertEqual(_classify(out),'fcf_observed_receipt_matched')
        self.assertFalse(s['official_six_pillar_100pt_certified'])
        with self.assertRaises(t.ReportedTotalError):recover(out)

    def test_known_components_are_preserved_and_conflicting_total_not_adopted(self):
        old=prior(ppe=300000000);out=recover(old)
        self.assertEqual(out['ppe_capex_krw'],300000000);self.assertIsNone(out['intangible_capex_krw'])
        bad=recover(old,payload(old,amount='200000000'))
        self.assertEqual(bad['reported_total_review']['reason'],'known_amount_conflict');self.assertIsNone(bad['free_cashflow_krw'])
        self.assertNotIn('reported_capex_total',bad)

    def test_only_literal_combined_cf_names_are_eligible(self):
        for name in ('유형자산및무형자산의 취득',' 유·무형자산의\t취득 ', '유형 및 무형자산 취득'):
            self.assertIsNotNone(t.canonical(name))
        for name in ('유형자산의 취득','무형자산의 취득','유무형자산 등의 취득','유무형자산 취득 및 처분',
                     '유형자산무형자산및기타비유동자산의취득','유무형자산의취득(단위:백만원)','유무형자산의취득\u200b'):
            self.assertIsNone(t.canonical(name))
        old=prior()
        for changes in ({'sj_div':'BS'},{'account_detail':'사업부별'},{'account_id':'dart_UnknownExtension'}):
            out=recover(old,payload(old,**changes));self.assertIsNone(out['free_cashflow_krw'])

    def test_wrong_company_year_scope_receipt_and_currency_fail_closed(self):
        old=prior()
        for changes in ({'corp_code':'99999999'},{'stock_code':'999999'},{'bsns_year':'2024'},
                        {'fs_div':'OFS'},{'rcept_no':'20260321000001'},{'currency':'USD'}):
            with self.assertRaises((t.ReportedTotalError,cf.CashflowError)):recover(old,payload(old,**changes))

    def test_zero_is_only_observed_when_explicit_and_negative_blank_or_conflict_rejects(self):
        old=prior();out=recover(old,payload(old,amount='0'))
        self.assertEqual(out['free_cashflow_krw'],1200000000);self.assertEqual(t.verify(out),0)
        for amount in ('','-','-1','400.5','NaN'):
            with self.assertRaises(t.ReportedTotalError):recover(old,payload(old,amount=amount))
        body=payload(old);body['list'].append({**body['list'][1],'thstrm_amount':'400000001'})
        with self.assertRaises(t.ReportedTotalError):recover(old,body)

    def test_cfo_anchor_or_existing_nonmissing_component_cannot_be_replaced(self):
        old=prior(ppe=300000000);body=payload(old);body['list'][0]['thstrm_amount']='1200000001'
        out=recover(old,body);self.assertEqual(out['reported_total_review']['reason'],'unverified_cfo');self.assertIsNone(out['free_cashflow_krw'])
        body=payload(old);body['list'].append({**body['list'][0],'account_id':'ifrs-full_PurchaseOfPropertyPlantAndEquipment','thstrm_amount':'300000001'})
        out=recover(old,body);self.assertEqual(out['reported_total_review']['reason'],'known_amount_conflict')

    def test_proof_tampering_is_rejected_by_both_source_and_ledger_verifiers(self):
        row=recover(prior())
        for key,value in [('scope','OFS'),('annual_receipt_id','20260321000001'),('total_capex_krw',1),('operating_cashflow_krw',1)]:
            bad=copy.deepcopy(row);bad[key]=value
            with self.assertRaises(t.ReportedTotalError):t.verify(bad)
            with self.assertRaises(cf.CashflowError):cf.summary(DAY,{CODE:{}},{CODE:{'scope':bad['scope'],'years':{'2025':bad}}},'a'*64)
        bad=copy.deepcopy(row);bad['reported_capex_total']['account_name']='유형자산의취득'
        with self.assertRaises(t.ReportedTotalError):t.verify(bad)

    def test_unavailable_review_preserves_nulls_and_stops_repeat_queries(self):
        old=prior();out=t.merge(old,None,None)
        self.assertIsNone(t.verify(out));self.assertIsNone(out['total_capex_krw']);self.assertIsNone(out['free_cashflow_krw'])
        state={'records':{CODE:{'scope':'CFS','years':{'2025':out}}}}
        self.assertEqual(t.targets(state,{CODE},8),[])
        with self.assertRaises(t.ReportedTotalSourceError) as error:
            t.parse({'status': '020', 'message': 'untrusted provider text'}, old, code=CODE, corp=CORP, day=DAY)
        self.assertEqual(str(error.exception), 'reported_total_provider_stop')
        self.assertEqual(error.exception.details, {'provider_status': '020'})

    def test_source_retries_transient_failures_once_without_leaking_request(self):
        class ReadTimeout(Exception):
            pass

        class HTTPError(Exception):
            response = SimpleNamespace(status_code=429)

        requests = SimpleNamespace(exceptions=SimpleNamespace(
            Timeout=ReadTimeout, ConnectionError=ConnectionError,
            HTTPError=HTTPError, RequestException=Exception))
        self.enterContext(patch.dict(sys.modules, {'requests': requests}))
        self.enterContext(patch.object(t.time, 'monotonic', return_value=0))
        self.enterContext(patch.object(t.time, 'sleep'))

        session = Mock()
        session.get.side_effect = [ReadTimeout('sensitive request URL'), Mock()]
        progress = {'provider_calls': 0}
        t.source_response(session, 'fnlttSinglAcntAll.json', 'mock-key', {}, deadline=300, progress=progress)
        self.assertEqual(progress['provider_calls'], 2)

        session.reset_mock()
        session.get.side_effect = None
        session.get.return_value.raise_for_status.side_effect = HTTPError('sensitive request URL')
        progress = {'provider_calls': 0}
        with self.assertRaises(t.ReportedTotalSourceError) as error:
            t.source_response(session, 'fnlttSinglAcntAll.json', 'mock-key', {}, deadline=300, progress=progress)
        self.assertEqual(str(error.exception), 'reported_total_http_status_failed')
        self.assertEqual(error.exception.details, {'http_status': 429})
        self.assertEqual(progress['provider_calls'], 1)

    def test_queue_prefers_completion_candidates_with_combined_missing_components(self):
        state={'records':{'000001':{'years':{'2025':prior(ppe=1)}},'000002':{'years':{'2025':prior()}}}}
        self.assertEqual(t.targets(state,{'000001','000002'},1)[0][0],'000002')
        self.assertEqual(t.targets(state,{'000001'},1)[0][0],'000001')

    def test_one_component_to_full_score_precedes_generic_both_missing_priorities(self):
        state={'records':{
            '000001':{'years':{'2023':prior(2023),'2024':prior(2024)}},
            '000002':{'years':{'2023':prior(2023,ppe=1)}}}}
        self.assertEqual(t.targets(state,{'000001'},1,{'000002'})[0][0],'000002')

    def test_sub90_manual_queue_never_falls_through_to_nonpriority_sources(self):
        state={'records':{
            '000001':{'years':{'2023':prior(2023)}},
            '000002':{'years':{'2023':prior(2023),'2024':prior(2024)}},
        }}
        self.assertEqual(t.targets(state,{'000001','000002'},40,
                                   threshold_priority={'000001'},
                                   only_sub90=True)[0][0], '000001')
        self.assertEqual(len(t.targets(state,{'000001','000002'},40,
                                       threshold_priority={'000001'},
                                       only_sub90=True)),1)
        reviewed=copy.deepcopy(state)
        reviewed['records']['000001']['years']['2023']=t.merge(prior(2023),None,None)
        self.assertEqual(t.targets(reviewed,{'000001','000002'},40,
                                   threshold_priority={'000001'},only_sub90=True),[])
        # General scheduled collection still preserves its wider recovery.
        self.assertEqual(t.targets(reviewed,{'000001','000002'},1)[0][0],'000002')

    def test_three_year_actual_total_receipts_replay_into_both_fcf_factors(self):
        market,catalog,bulk=live_inputs();records={}
        for y in (2023,2024,2025):records[str(y)]=recover(prior(y))
        for y in (2023,2024,2025):bulk['records'][CODE]['years'][str(y)]['CFS']['values']['assets']=10000000000
        state=cf.summary(catalog['date'],{r['code']:r for r in catalog['rows']},{CODE:{'scope':'CFS','years':records}},cf.fingerprint(bulk))
        cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True)
        features=factor.real_features(catalog,bulk,state)[CODE]
        self.assertEqual(features['financial_fcf_assets_3y_pct'],8.0)
        self.assertIsNotNone(features['valuation_fcf_yield_pct'])
        self.assertEqual(state['metrics']['three_year_fcf_receipt_matched_issuers'],1)

    def test_component_rechecks_preserve_the_independent_reported_total(self):
        old=recover(prior())
        observed=cf.normalize_year(rows(2025,CODE,skip_intangible=True),code=CODE,corp=CORP,year=2025,scope='CFS',asof=DAY,major_receipt=old['annual_receipt_id'])
        new=cf.merge_account_name_recheck(old,observed)
        self.assertEqual(new['total_capex_krw'],400000000);self.assertEqual(new['free_cashflow_krw'],800000000)
        self.assertEqual(new['ppe_capex_krw'],300000000);self.assertIsNone(new['intangible_capex_krw'])
        self.assertEqual(t.verify(new),400000000)
