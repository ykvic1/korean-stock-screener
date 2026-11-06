"""Issuer-local rejection must preserve progress without false observations."""
import copy
import unittest

import v3_dart_2025_owner_profit_probe as p
from v3_dart_bulk_history import make_company_mapping_cache
import test_v3_dart_2025_owner_profit_probe as fixtures


class QuarantineTests(unittest.TestCase):
    def source(self,reason):
        catalog,book,priority=fixtures.inputs()
        day,members,expected,_,selected=p.plan(catalog,book,priority)
        mapping={c:str(12345678+i) for i,c in enumerate(selected)}
        reverse={v:k for k,v in mapping.items()}
        class Provider:
            def __init__(self):self.codes=[]
            def get(self,url,params,timeout):
                code=reverse[params['corp_code']];self.codes.append(code)
                row=fixtures.OwnerProfitSourceTests().row(expected[code],'100')
                row['corp_code']=params['corp_code']
                rows=[row]
                if code==selected[0]:
                    if reason=='annual_profit_currency_not_krw':row['currency']='USD'
                    elif reason=='annual_profit_receipt_not_matched':row['rcept_no']='20260321000001'
                    elif reason=='annual_profit_amount_not_integer_krw':row['thstrm_amount']='not-an-amount'
                    elif reason=='conflicting_official_owner_profit_accounts':
                        rows.append({**row,'account_id':sorted(p.PROFIT_IDS['CFS'])[1],'thstrm_amount':'200'})
                    else:return fixtures.Result({'status':reason,'message':'do not export provider messages'})
                return fixtures.Result({'status':'000','list':rows})
        return catalog,book,priority,day,members,selected,mapping,Provider()

    def test_all_issuer_account_rejections_continue_and_never_create_zero(self):
        for reason in sorted(p.QUARANTINED_REASONS):
            with self.subTest(reason=reason):
                catalog,book,priority,day,members,selected,mapping,source=self.source(reason)
                saved=[]
                report,calls=p.collect(catalog,book,priority,None,key='synthetic-test-not-credential',
                    session=source,cache=make_company_mapping_cache(mapping,day,members),on_progress=saved.append)
                self.assertEqual(calls,2);self.assertEqual(source.codes,selected)
                self.assertEqual(len(saved),2)
                bad=report['records'][selected[0]]
                self.assertEqual(bad['status'],'quarantined_source_account')
                self.assertIsNone(bad['owners_profit_krw']);self.assertIsNone(bad['source_account_id'])
                self.assertEqual(bad['source_rejection_reason'],reason)
                self.assertEqual(report['observed_official_owner_profit_issuers'],1)
                self.assertEqual(report['quarantined_source_account_issuers'],1)
                self.assertEqual(report['missing_standard_account_issuers'],0)
                self.assertTrue(p.verify(report,catalog,book,priority))
                again,requests=p.collect(catalog,book,priority,report,key=None,session=source)
                self.assertEqual(requests,0);self.assertEqual(again,report)
                self.assertEqual(source.codes,selected)
                forged=copy.deepcopy(report)
                forged['records'][selected[0]]['owners_profit_krw']=0
                with self.assertRaises(p.ProfitProbeError):p.verify(forged,catalog,book,priority)

    def test_quota_and_unknown_provider_errors_stop_without_quarantine(self):
        for status in ('020','800','900','999'):
            catalog,book,priority,day,members,selected,mapping,source=self.source(status)
            saved=[]
            with self.assertRaisesRegex(p.ProfitProbeError,'official_profit_provider_status_unavailable'):
                p.collect(catalog,book,priority,None,key='synthetic-test-not-credential',session=source,
                    cache=make_company_mapping_cache(mapping,day,members),on_progress=saved.append)
            self.assertEqual(source.codes,selected[:1]);self.assertFalse(saved)

    def test_unknown_quarantine_reason_is_rejected(self):
        catalog,book,priority,day,_,expected=self.source('020')[:6]
        _,_,bases,_,_=p.plan(catalog,book,priority)
        row={**bases[expected[0]],'observed_on':day,'raw_provider_rows_published':False,
            'status':'quarantined_source_account','owners_profit_krw':None,'source_account_id':None,
            'source_rejection_reason':'do not accept arbitrary provider text'}
        with self.assertRaisesRegex(p.ProfitProbeError,'quarantine_reason'):
            p._validated_record(row,day)
