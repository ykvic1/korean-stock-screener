import copy
import unittest
from unittest.mock import patch
import io
import zipfile
from collections import Counter
import v3_dart_xbrl_owner_profit as r
import v3_dart_2025_owner_profit_probe as p
from test_v3_dart_xbrl_capex_pilot import archive
from test_v3_dart_2025_owner_profit_probe import inputs

NS='http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'

def xml(*,member='EquityAttributableToOwnersOfParentMember',value='80',cfo='100',scope='ConsolidatedMember',extra='',direct=None,concept='ProfitLoss',currency='iso:KRW',identity='00123456'):
    direct_fact='' if direct is None else f'<i:ProfitLossAttributableToOwnersOfParent contextRef="C" unitRef="U">{direct}</i:ProfitLossAttributableToOwnersOfParent>'
    return f'''<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:i="{NS}" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217">
<context id="C"><entity><identifier scheme="http://dart.fss.or.kr">{identity}</identifier></entity><period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period><scenario><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:{scope}</d:explicitMember></scenario></context>
<context id="P"><entity><identifier scheme="http://dart.fss.or.kr">{identity}</identifier></entity><period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period><scenario><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:{scope}</d:explicitMember><d:explicitMember dimension="i:ComponentsOfEquityAxis">i:{member}</d:explicitMember>{extra}</scenario></context>
<unit id="U"><measure>{currency}</measure></unit><i:CashFlowsFromUsedInOperatingActivities contextRef="C" unitRef="U">{cfo}</i:CashFlowsFromUsedInOperatingActivities><i:{concept} contextRef="P" unitRef="U">{value}</i:{concept}>{direct_fact}</xbrl>'''.encode()


def extract(raw,scope='CFS'):
    return r.extract_archive(raw,scope=scope,identifiers={'00123456'},cfo_anchor=100)


def missing():
    c,b,q=inputs();day,_,expected,_,_=p.plan(c,b,q)
    return p.parse_response({'status':'013'},corp='00123456',base=expected['000001'],on_day=day)


def merge(row,value=80,bases=None,reason='no_verified_owner_profit'):
    return r.merge(row,value,bases or ['parent_equity_profit_loss'],reason=reason,cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)


class OriginalOwnerProfitTests(unittest.TestCase):
    def test_reported_total_parent_equity_profit_is_adopted_and_source_replays(self):
        value,bases,_=extract(archive(xml()));self.assertEqual((value,bases),(80,['parent_equity_profit_loss']))
        row=merge(missing(),value,bases);self.assertTrue(r.verify_review(row));self.assertEqual(row['source_account_id'],r.DIMENSIONAL_ID)
        c,b,q=inputs();state=p.snapshot(c['date'],c,b,q,{'000001':row});self.assertTrue(p.verify(state,c,b,q))
        self.assertEqual(state['original_xbrl_recovered_issuers'],1);self.assertEqual(state['observed_official_owner_profit_issuers'],1)
        self.assertFalse(state['investment_grade_score_certified'])
        self.assertEqual(p.plan(c,b,q,state)[-1],['000002'])

    def test_nci_retained_earnings_reserves_other_dimensions_and_comprehensive_income_are_excluded(self):
        for changes in ({'member':'NoncontrollingInterestsMember'},{'member':'RetainedEarningsMember'}, {'member':'OtherReservesMember'}, {'concept':'ComprehensiveIncome'}, {'extra':'<d:explicitMember dimension="i:OtherAxis">i:OtherMember</d:explicitMember>'}):
            self.assertIsNone(extract(archive(xml(**changes)))[0])

    def test_entity_scope_currency_and_annual_period_must_match(self):
        for changes in ({'identity':'00999999'}, {'scope':'SeparateMember'}, {'currency':'iso:USD'}):
            self.assertIsNone(extract(archive(xml(**changes)))[0])
        self.assertIsNone(extract(archive(xml().replace(b'2025-12-31',b'2025-09-30')))[0])
        bad=xml().replace(NS.encode(),b'http://untrusted.example/ifrs-full');self.assertIsNone(extract(archive(bad))[0])

    def test_direct_and_dimensional_facts_must_agree_and_invalid_cfo_or_fact_rejects(self):
        for changes in ({'cfo':'101'},{'cfo':''},{'value':''},{'value':'1.5'},{'direct':'81'}):
            with self.assertRaises(r.OriginalProfitError):extract(archive(xml(**changes)))
        value,bases,_=extract(archive(xml(direct='80')));self.assertEqual(value,80);self.assertEqual(len(bases),2)
        row=merge(missing(),value,bases);self.assertIn(row['source_account_id'],p.PROFIT_IDS['CFS'])

    def test_explicit_zero_and_loss_are_reported_values_no_absent_profit_zero(self):
        for value in ('0','-100'):
            observed,bases,_=extract(archive(xml(value=value)));self.assertEqual(observed,int(value));self.assertTrue(r.verify_review(merge(missing(),observed,bases)))
        raw=xml().replace(b'<i:ProfitLoss contextRef="P" unitRef="U">80</i:ProfitLoss>',b'')
        self.assertIsNone(extract(archive(raw))[0])
        row=merge(missing(),None,reason='no_verified_owner_profit');self.assertIsNone(row['owners_profit_krw']);self.assertFalse(r.verify_review(row))

    def test_proof_receipt_scope_amount_and_basis_tampering_and_overwrites_reject(self):
        row=merge(missing())
        for key,value in [('annual_receipt_id','20260321000001'),('fs_div','OFS'),('owners_profit_krw',81)]:
            bad=copy.deepcopy(row);bad[key]=value
            with self.assertRaises(r.OriginalProfitError):r.verify_review(bad)
        bad=copy.deepcopy(row);bad.pop('original_xbrl_proof')
        with self.assertRaises(r.OriginalProfitError):p._validated_record(bad,bad['observed_on'])
        with self.assertRaises(r.OriginalProfitError):merge(row)
        bad=copy.deepcopy(row);bad['source_account_id']='ifrs-full_ProfitLoss'
        with self.assertRaises(r.OriginalProfitError):r.verify_review(bad)

    def test_quarantined_flat_source_history_is_preserved_by_independent_original_proof(self):
        row=missing();row.update(status='quarantined_source_account',source_rejection_reason='annual_profit_amount_not_integer_krw')
        out=merge(row);self.assertEqual(out['source_rejection_reason'],row['source_rejection_reason']);p._validated_record(out,out['observed_on'])

    def test_default_context_reader_still_rejects_parent_equity_dimensions(self):
        from v3_dart_xbrl_cashflow_recovery import verified_contexts
        _,_,plain=verified_contexts(xml(),year=2025,scope='CFS',identifiers={'00123456'})
        _,_,parent=verified_contexts(xml(),year=2025,scope='CFS',identifiers={'00123456'},equity_owner=True)
        self.assertTrue(plain['C']);self.assertFalse(plain['P']);self.assertTrue(parent['P']);self.assertFalse(parent['C'])

    def test_independent_scope_cfo_api_anchor_never_borrows_other_scope_or_receipt(self):
        base=missing()
        row={'corp_code':'00123456','stock_code':base['code'],'bsns_year':'2025','reprt_code':'11011',
             'fs_div':'CFS','sj_div':'CF','account_detail':'-','currency':'KRW',
             'rcept_no':base['annual_receipt_id'],'account_id':'ifrs-full_CashFlowsFromUsedInOperatingActivities','thstrm_amount':'100'}
        kwargs=dict(code=base['code'],corp='00123456',scope='CFS',receipt=base['annual_receipt_id'],day=base['observed_on'])
        value,digest=r.official_cfo_anchor({'status':'000','list':[row]},**kwargs)
        self.assertEqual(value,100);self.assertEqual(len(digest),64)
        adopted=r.merge(base,80,['parent_equity_profit_loss'],reason='no_verified_owner_profit',cfo_anchor=value,archive_hash='a'*64,profile_hash='b'*64,cfo_response_hash=digest)
        self.assertTrue(r.verify_review(adopted));self.assertEqual(adopted['original_xbrl_proof']['cfo_response_sha256'],digest)
        for key,value in [('fs_div','OFS'),('currency','USD'),('corp_code','00999999')]:
            with self.assertRaises(Exception):r.official_cfo_anchor({'status':'000','list':[{**row,key:value}]},**kwargs)
        self.assertEqual(r.official_cfo_anchor({'status':'000','list':[{**row,'rcept_no':'20260321000001'}]},**kwargs),(None,None))
        self.assertEqual(r.official_cfo_anchor({'status':'013'},**kwargs),(None,None))
        with self.assertRaises(r.OriginalProfitError):r.official_cfo_anchor({'status':'020'},**kwargs)

    def test_missing_independent_cfo_is_only_an_unavailable_review(self):
        row=r.merge(missing(),None,[],reason='no_verified_cfo_scope',cfo_anchor=None,archive_hash=None,profile_hash=None)
        self.assertIsNone(row['owners_profit_krw']);self.assertNotIn('original_xbrl_proof',row)
        self.assertFalse(r.verify_review(row));p._validated_record(row,row['observed_on'])

    def test_unrelated_extension_research_never_blocks_standard_profit_facts(self):
        raw=archive(xml())
        with patch.object(r.xp,'_extension_label_evidence',side_effect=r.xp.XbrlPilotError('ambiguous_local_presentation_locator')):
            self.assertEqual(extract(raw)[0],80)
            with self.assertRaises(r.xp.XbrlPilotError):r.xp.inspect_binary_archive(raw,year=2025)
        with self.assertRaises(r.xp.XbrlPilotError):r.xp.inspect_binary_archive(raw,year=2025,include_extension_evidence=1)

    def test_archive_safety_bounds_remain_mandatory_without_extension_research(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('../unsafe.xml',xml())
        with self.assertRaises(r.xp.XbrlPilotError):extract(stream.getvalue())
        dangerous=xml().replace(b'<xbrl',b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><xbrl',1)
        with self.assertRaises(r.xp.XbrlPilotError):extract(archive(dangerous))

    def test_prior_observation_and_newer_recheck_keep_their_actual_dates(self):
        row=missing();row['observed_on']='2026-10-28'
        row['profit_recheck_contract']=p.profit_allocation.CONTRACT;row['profit_recheck_on']='2026-11-02'
        out=r.merge(row,80,['parent_equity_profit_loss'],reason='no_verified_owner_profit',cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64,asof='2026-11-02')
        self.assertEqual(out['observed_on'],'2026-10-28');self.assertEqual(out['profit_recheck_on'],'2026-11-02')
        p._validated_record(out,'2026-11-02')
