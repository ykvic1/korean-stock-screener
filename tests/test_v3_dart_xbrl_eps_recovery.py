import copy
import unittest
from collections import Counter
import v3_dart_xbrl_eps_recovery as r
import v3_dart_reported_eps as eps
from test_v3_dart_xbrl_capex_pilot import archive
from test_v3_dart_reported_eps import BASE,DAY,inputs
from v3_operational_research_score import verified_reported_eps

NS='http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'

def xml(*,member='ConsolidatedMember',identity='00123456',cfo='100',den='x:shares',concept='BasicEarningsLossPerShare',values=('-20','0','150.25'),currency='iso:KRW',namespace=NS):
    contexts=''.join(f'<context id="C{y}"><entity><identifier scheme="http://dart.fss.or.kr">{identity}</identifier></entity><period><startDate>{y}-01-01</startDate><endDate>{y}-12-31</endDate></period><scenario><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:{member}</d:explicitMember></scenario></context>' for y in (2023,2024,2025))
    facts=''.join(f'<i:{concept} contextRef="C{y}" unitRef="E">{v}</i:{concept}>' for y,v in zip((2023,2024,2025),values))
    return f'''<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:x="http://www.xbrl.org/2003/instance" xmlns:i="{namespace}" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217" xmlns:bad="http://untrusted.example">{contexts}<unit id="U"><measure>{currency}</measure></unit><unit id="E"><divide><unitNumerator><measure>{currency}</measure></unitNumerator><unitDenominator><measure>{den}</measure></unitDenominator></divide></unit><i:CashFlowsFromUsedInOperatingActivities contextRef="C2025" unitRef="U">{cfo}</i:CashFlowsFromUsedInOperatingActivities>{facts}</xbrl>'''.encode()


def extract(raw,scope='CFS'):
    return r.extract_archive(raw,scope=scope,identifiers={'00123456'},cfo_anchor=100)


def missing():
    row=eps.parse({'status':'013'},corp='00123456',base=BASE,on_day=DAY)
    row['source_name_recheck']={'version':eps.NAME_RECHECK_VERSION,'receipt_id':BASE['annual_receipt_id'],'result':'checked_unavailable'}
    return row


def merge(row,values,reason='no_additional_reported_basic_eps'):
    return r.merge(row,values,reason=reason,cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)


class OriginalEPSTests(unittest.TestCase):
    def test_reported_negative_zero_fractional_basic_eps_and_replay(self):
        values,_=extract(archive(xml()));self.assertEqual(values,{'2023':-20.,'2024':0.,'2025':150.25})
        row=merge(missing(),values);self.assertTrue(r.verify_review(row))
        catalog,book=inputs();state=eps.snapshot(DAY,catalog,book,{BASE['code']:BASE},{BASE['code']:row})
        self.assertEqual(eps.plan(catalog,book,state)[-1],[])
        self.assertEqual(state['complete_three_year_eps_issuers'],1)
        self.assertEqual(state['original_xbrl_recovered_issuers'],1)
        self.assertEqual(row['source_name_recheck']['result'],'checked_unavailable')
        self.assertIn(BASE['code'],verified_reported_eps(catalog,book,state))
        self.assertFalse(state['investment_score_certified'])
        with self.assertRaises(r.OriginalEPSError):merge(row,values)

    def test_wrong_identity_scope_currency_namespace_and_diluted_only_never_recover(self):
        for kw in ({'identity':'00999999'},{'member':'SeparateMember'}, {'currency':'iso:USD'}, {'namespace':'http://untrusted.example/ifrs-full'}, {'concept':'DilutedEarningsLossPerShare'},{'den':'bad:shares'}):
            values,_=extract(archive(xml(**kw)));self.assertEqual(values,{})
        self.assertEqual(extract(archive(xml(member='SeparateMember')),scope='OFS')[0]['2025'],150.25)

    def test_invalid_cfo_blank_nil_and_conflicting_eps_reject(self):
        for kw in ({'cfo':'101'},{'cfo':''},{'values':('-20','','150.25')},{'values':('-20','0','NaN')}):
            with self.assertRaises((r.OriginalEPSError,eps.EPSError)):extract(archive(xml(**kw)))
        bad=xml().replace(b'</xbrl>',b'<i:BasicEarningsLossPerShare contextRef="C2025" unitRef="E">151</i:BasicEarningsLossPerShare></xbrl>')
        with self.assertRaises(r.OriginalEPSError):extract(archive(bad))
        nil=xml().replace(b'<i:BasicEarningsLossPerShare contextRef="C2025"',b'<i:BasicEarningsLossPerShare xmlns:n="http://www.w3.org/2001/XMLSchema-instance" n:nil="true" contextRef="C2025"')
        with self.assertRaises(r.OriginalEPSError):extract(archive(nil))

    def test_wrong_annual_period_missing_comparative_and_unverified_units_stay_missing(self):
        raw=xml().replace(b'<i:BasicEarningsLossPerShare contextRef="C2023" unitRef="E">-20</i:BasicEarningsLossPerShare>',b'')
        values,_=extract(archive(raw));self.assertNotIn('2023',values)
        row=merge(missing(),values);self.assertFalse(row['metrics']['complete_three_years']);self.assertIsNone(row['reported_basic_eps_2023_2025'][0])
        raw=xml().replace(b'2024-12-31',b'2024-09-30');self.assertNotIn('2024',extract(archive(raw))[0])
        raw=xml().replace(b'unitRef="E"',b'unitRef="U"');self.assertEqual(extract(archive(raw))[0],{})

    def test_existing_values_and_receipt_scope_hash_proofs_are_immutable(self):
        row=missing();row['reported_basic_eps_2023_2025']=[None,None,151.]
        with self.assertRaises(r.OriginalEPSError):merge(row,{'2025':150.25})
        row=merge(missing(),{'2023':1.,'2024':2.,'2025':3.})
        for key,value in [('annual_receipt_id','20260319000001'),('fs_div','OFS'),('reported_basic_eps_2023_2025',[1.,2.,4.])]:
            bad=copy.deepcopy(row);bad[key]=value
            with self.assertRaises(r.OriginalEPSError):r.verify_review(bad)
        bad=copy.deepcopy(row);bad['original_xbrl_proof']['archive_sha256']='bad'
        with self.assertRaises(r.OriginalEPSError):r.verify_review(bad)

    def test_unavailable_review_is_not_observed_zero(self):
        row=merge(missing(),{},'no_verified_cfo_scope');self.assertFalse(r.verify_review(row));self.assertEqual(row['reported_basic_eps_2023_2025'],[None]*3)
        catalog,book=inputs();state=eps.snapshot(DAY,catalog,book,{BASE['code']:BASE},{BASE['code']:row});eps.plan(catalog,book,state)
        bad=copy.deepcopy(row);bad['original_xbrl_review']['receipt_id']='20260319000001'
        with self.assertRaises(r.OriginalEPSError):r.verify_review(bad)
