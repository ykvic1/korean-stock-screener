import copy
import unittest
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
