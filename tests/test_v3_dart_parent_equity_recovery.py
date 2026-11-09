"""No-network proofs for parent equity and current-cap PBR replay."""
import copy
import json
from pathlib import Path
import unittest

import v3_dart_parent_equity_recovery as source
from v3_dart_bulk_history import research_parts
from v3_current_observed_scorecard import finance_source


ROOT=Path(__file__).resolve().parent.parent/'docs'


def data():
    read=lambda n:json.loads((ROOT/n).read_text())
    catalog,book,cash,major=[read(x) for x in (
        'v22-market-catalog.json','v3-dart-book-reference.json',
        'v3-dart-cashflow-reference.json','v3-dart-bulk-history.json')]
    code='069510'
    annual=next(x['annual'] for x in book['records'] if x['code']==code)
    assert annual['values']['owners_equity'] is None
    return catalog,book,cash,major,code,annual


def payload(code,annual,nci,corp='00100001'):
    common={'corp_code':corp,'stock_code':code,'bsns_year':'2025','reprt_code':'11011',
            'fs_div':'CFS','rcept_no':annual['receipt_id'],'sj_div':'BS',
            'account_detail':'-','currency':'KRW'}
    return {'status':'000','list':[
        {**common,'account_id':'ifrs-full_Equity',
         'thstrm_amount':str(annual['values']['equity'])},
        {**common,'account_id':'ifrs-full_NoncontrollingInterests',
         'thstrm_amount':str(nci)}]}


class ParentEquityRecoveryTests(unittest.TestCase):
    def checked(self):
        catalog,book,cash,major,code,annual=data()
        nci=100000000
        original=source.blank(catalog['date'])
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        out,why=source.merge(original,code,annual,cfo,payload(code,annual,nci),
                             corp='00100001',day=catalog['date'],
                             catalog=catalog,book=book,cash=cash)
        self.assertEqual(why,'verified_reported_parent_equity')
        return out,catalog,book,cash,major,code,annual,nci

    def test_only_explicit_reported_nci_is_provable_and_replays_current_cap(self):
        saved,catalog,book,cash,major,code,annual,nci=self.checked()
        self.assertEqual(source.verify(saved,catalog,book,cash),1)
        self.assertFalse(saved['investment_score_certified'])
        self.assertFalse(saved['raw_provider_rows_published'])
        self.assertEqual(saved['records'][code]['explicit_nci_krw'],nci)
        self.assertNotIn('corp_code',str(saved))
        raw_before=copy.deepcopy(book)
        overlaid=source.overlay(book,catalog,saved,cash)
        self.assertEqual(book,raw_before,'standalone book cannot be rewritten')
        obs=next(r for r in overlaid['observations'] if r['code']==code)
        self.assertGreater(obs['pbr_simple_annual_reference'],0)
        raw=next(r for r in book['observations'] if r['code']==code)
        self.assertIsNone(raw['pbr_simple_annual_reference'])
        new={r['code']:r for r in research_parts(major,catalog,overlaid)['rows']}
        self.assertIsNotNone(new[code]['unverified_components']['pbr']['candidate_points'])

    def test_absent_nci_never_mints_pbr_evidence(self):
        catalog,book,cash,_,code,annual=data()
        body=payload(code,annual,1)
        body['list'].pop()
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        result,why=source.merge(source.blank(catalog['date']),code,annual,cfo,body,
                                corp='00100001',day=catalog['date'],
                                catalog=catalog,book=book,cash=cash)
        self.assertEqual(why,'neither_owner_nor_explicit_nci')
        self.assertEqual(result['records'],{})

    def test_current_finance_replay_accepts_verified_supplement_without_book_mutation(self):
        saved,catalog,book,cash,major,code,*_=self.checked()
        parts=json.loads((ROOT/'v3-dart-bulk-provisional.json').read_text())
        audit=json.loads((ROOT/'v3-real-source-six-area-coverage.json').read_text())
        got=finance_source(catalog,major,parts,book,audit,
                           parent_equity_source=saved,parent_equity_cash=cash)
        self.assertIsNotNone(got)
        self.assertTrue(got[code]['parent_equity_2025_source_replayed'])
        self.assertIsNotNone(got[code]['unverified_components']['pbr']['candidate_points'])

    def test_tampering_receipt_nci_cfo_or_amount_rejected(self):
        saved,catalog,book,cash,*_=self.checked()
        code='069510'
        for field,value in [('receipt_id','20260321000099'),
                            ('explicit_nci_krw',-50000000),
                            ('cfo_anchor_krw',1),
                            ('owners_equity_krw',3),
                            ('provider_sha256','not_a_sha')]:
            forged=copy.deepcopy(saved);forged['records'][code][field]=value
            with self.subTest(field=field),self.assertRaises(source.ParentEquityRecoveryError):
                source.verify(forged,catalog,book,cash)
        future=copy.deepcopy(saved);future['observed_on']='2030-01-01'
        with self.assertRaises(source.ParentEquityRecoveryError):
            source.verify(future,catalog,book,cash)



class ExpandedParentEquityCollectionTests(unittest.TestCase):
    def test_reported_direct_owner_fact_does_not_invent_an_nci_amount(self):
        catalog,book,cash,_,code,annual=data();body=payload(code,annual,0)
        body['list'][1]['account_id']='ifrs-full_EquityAttributableToOwnersOfParent'
        body['list'][1]['thstrm_amount']=str(annual['values']['equity']-100)
        out,reason=source.merge(source.blank(catalog['date']),code,annual,
            cash['records'][code]['years']['2025']['operating_cashflow_krw'],body,
            corp='00100001',day=catalog['date'],catalog=catalog,book=book,cash=cash)
        proof=out['records'][code]
        self.assertEqual(reason,'verified_reported_parent_equity')
        self.assertEqual(proof['contract'],source.DIRECT_CONTRACT)
        self.assertIsNone(proof['explicit_nci_krw'])
        self.assertEqual(source.verify(out,catalog,book,cash),1)
        proof['explicit_nci_krw']=0
        proof['digest']=source.cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})
        with self.assertRaises(source.ParentEquityRecoveryError):source.verify(out,catalog,book,cash)

    def test_accumulated_proofs_are_not_limited_by_one_capture_batch(self):
        catalog,book,cash,_,_,_=data()
        report=json.loads((ROOT/'v3-operational-research-scores.json').read_text())
        comparison=json.loads((ROOT/'v3-operational-research-comparison.json').read_text())
        # Keep the selector independent of incidental published score coverage.
        import v3_dart_parent_equity_pilot as pilot
        annuals=source._annuals(book);members={r['code'] for r in catalog['rows']}
        chosen=[]
        for code,a in annuals.items():
            anchor=cash.get('records',{}).get(code,{}).get('years',{}).get('2025',{})
            if (a and code in members and a.get('fs_div')=='CFS' and a.get('year')==2025
                and a.get('currency')=='KRW' and a['values'].get('owners_equity') is None
                and type(a['values'].get('equity')) is int and a['values']['equity']>0
                and anchor.get('major_annual_receipt_matched') is True and anchor.get('scope')=='CFS'
                and anchor.get('annual_receipt_id')==a.get('receipt_id') and type(anchor.get('operating_cashflow_krw')) is int):chosen.append((code,a))
        self.assertGreaterEqual(len(chosen),8)
        saved=source.blank(catalog['date'])
        for code,a in chosen[:8]:
            saved,_=source.merge(saved,code,a,cash['records'][code]['years']['2025']['operating_cashflow_krw'],
                payload(code,a,0),corp='00100001',day=catalog['date'],catalog=catalog,book=book,cash=cash)
        self.assertEqual(source.verify(saved,catalog,book,cash),8)
        self.assertLessEqual(source.MAX_CAPTURE,16)

    def test_negative_review_reused_only_for_unchanged_annual_and_cashflow_anchors(self):
        from unittest.mock import patch
        catalog,book,cash,_,code,annual=data()
        report=json.loads((ROOT/'v3-operational-research-scores.json').read_text())
        comp=json.loads((ROOT/'v3-operational-research-comparison.json').read_text())
        review={'code':code,'input_digest':source.input_digest(code,annual,cash),
            'reason':'neither_owner_nor_explicit_nci','provider_sha256':'a'*64,'observed_on':catalog['date']}
        review['digest']=source.cf.fingerprint(review)
        state={'schema':source.REVIEW_SCHEMA,'records':{code:review}}
        with patch.object(source.pilot,'eligible_targets',return_value=([(code,annual)],1)):
            queue,_,_=source.pending(report,comp,catalog,book,cash,source.blank(catalog['date']),state)
            self.assertEqual(queue,[])
            changed=copy.deepcopy(cash);changed['records'][code]['years']['2025']['operating_cashflow_krw']+=1
            queue,_,_=source.pending(report,comp,catalog,book,changed,source.blank(catalog['date']),state)
            self.assertEqual(len(queue),1)
        state['records'][code]['reason']='verified_reported_parent_equity'
        state['records'][code]['digest']=source.cf.fingerprint({k:v for k,v in review.items() if k!='digest'})
        with self.assertRaises(source.ParentEquityRecoveryError):
            source.pending(report,comp,catalog,book,cash,source.blank(catalog['date']),state)
        state['records'][code]['reason']='parent_equity_provider_unavailable'
        state['records'][code]['digest']=source.cf.fingerprint({k:v for k,v in review.items() if k!='digest'})
        with self.assertRaises(source.ParentEquityRecoveryError):source.verify_reviews(state,catalog)

    def test_workflow_continues_finite_queue_without_data_refresh_tests(self):
        text=(ROOT.parent/'.github/workflows/v3-parent-equity-recovery.yml').read_text()
        self.assertIn('v3-dart-parent-equity-reviews.json',text)
        self.assertIn("steps.collect.outputs.reviewed != '0'",text)
        self.assertIn('head_repository.full_name == github.repository',text)
        self.assertNotIn('python -m unittest',text)


if __name__=='__main__':unittest.main()
