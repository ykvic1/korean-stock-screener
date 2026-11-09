import copy
import unittest
from v3_dart_xbrl_cashflow_recovery import instance_values,archive_values,bounded_archive_values,merge,verify_recovery,company_ids,targets,cashflow_90_focus,RecoveryError
from test_v3_dart_xbrl_capex_pilot import archive

NS='http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'

def xml(*,member='ConsolidatedMember',identity='00123456',cfo='100',ppe='20',intang='0',end='2024-12-31',extra='',unit='iso:KRW',namespace=NS):
    return f'''<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:i="{namespace}"
        xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217">
      <context id="C"><entity><identifier scheme="http://dart.fss.or.kr">{identity}</identifier></entity>
        <period><startDate>2024-01-01</startDate><endDate>{end}</endDate></period>
        <scenario><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:{member}</d:explicitMember>{extra}</scenario></context>
      <unit id="U"><measure>{unit}</measure></unit>
      <i:CashFlowsFromUsedInOperatingActivities contextRef="C" unitRef="U">{cfo}</i:CashFlowsFromUsedInOperatingActivities>
      <i:PurchaseOfPropertyPlantAndEquipment contextRef="C" unitRef="U">{ppe}</i:PurchaseOfPropertyPlantAndEquipment>
      <i:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">{intang}</i:PurchaseOfIntangibleAssets></xbrl>'''.encode()


def extract(data,scope='CFS'):
    return instance_values(data,year=2024,scope=scope,identifiers={'00123456'},expected_cfo=100)


def prior():
    return {'year':2024,'scope':'CFS','annual_receipt_id':'20250318000001','major_annual_receipt_matched':True,
            'operating_cashflow_krw':100,'ppe_capex_krw':20,'intangible_capex_krw':None,
            'total_capex_krw':None,'free_cashflow_krw':None,'raw_source_account_rows_published':False,'historical_PIT_certified':False}


class OriginalRecoveryTests(unittest.TestCase):
    def test_reused_instance_rechecks_year_scope_and_issuer_without_reparsing(self):
        from unittest.mock import patch
        import v3_dart_xbrl_cashflow_recovery as c
        root,bindings,initial=c.verified_contexts(xml(),year=2024,scope='CFS',identifiers={'00123456'})
        with patch.object(c.xp,'_safe_xbrl_xml',side_effect=AssertionError('unexpected XML reparse')):
            for year,scope,ids,expected in ((2024,'CFS',{'00123456'},True),
                    (2023,'CFS',{'00123456'},False),(2024,'OFS',{'00123456'},False),
                    (2024,'CFS',{'00999999'},False)):
                selected=c.contexts_from_instance(root,bindings,year=year,scope=scope,identifiers=ids)
                self.assertEqual(any(selected.values()),expected)
        self.assertEqual(initial,{'C':True})

    def test_explicit_scope_entity_cfo_and_reported_zero_recover_only_actual_fact(self):
        values=extract(xml())
        self.assertEqual(values,{'operating_cashflow_krw':100,'ppe_capex_krw':20,'intangible_capex_krw':0})
        p=prior();out=merge(p,values,reason='no_missing_supported_fact',archive_hash='a'*64,profile_hash='b'*64)
        self.assertTrue(verify_recovery(out));self.assertEqual(out['free_cashflow_krw'],80)
        self.assertIsNone(p['intangible_capex_krw'])
        self.assertEqual(out['original_xbrl_proof']['fields'],{'intangible_capex_krw':0})
        with self.assertRaises(RecoveryError):merge(out,values,reason='recovered',archive_hash='a'*64,profile_hash='b'*64)

    def test_scope_entity_period_currency_and_namespace_cannot_be_guessed(self):
        for changes in ({'member':'SeparateMember'},{'identity':'00999999'},
                        {'end':'2024-09-30'},{'unit':'iso:USD'},{'namespace':'http://untrusted.example/ifrs-full'},
                        {'extra':'<d:explicitMember dimension="i:OtherAxis">i:OtherMember</d:explicitMember>'}):
            self.assertEqual(extract(xml(**changes)),{})
        self.assertEqual(extract(xml(member='SeparateMember'),scope='OFS')['intangible_capex_krw'],0)

    def test_known_cfo_malformed_or_conflicting_purchase_is_not_a_repair(self):
        for changes in ({'cfo':'101'},{'cfo':''},{'intang':''},{'intang':'-1'},{'intang':'0.5'}):
            with self.assertRaises(RecoveryError):extract(xml(**changes))
        bad=xml().replace(b'</xbrl>',b'<i:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">30</i:PurchaseOfIntangibleAssets></xbrl>')
        with self.assertRaises(RecoveryError):extract(bad)
        missing=xml().replace(b'<i:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">0</i:PurchaseOfIntangibleAssets>',b'')
        self.assertNotIn('intangible_capex_krw',extract(missing))

    def test_same_context_anchor_and_proof_tampering_are_rejected(self):
        without=xml().replace(b'<i:CashFlowsFromUsedInOperatingActivities contextRef="C" unitRef="U">100</i:CashFlowsFromUsedInOperatingActivities>',b'')
        self.assertEqual(extract(without),{})
        out=merge(prior(),extract(xml()),reason='no_missing_supported_fact',archive_hash='a'*64,profile_hash='b'*64)
        for key,value in [('scope','OFS'),('intangible_capex_krw',1),('annual_receipt_id','20250319000001')]:
            bad=copy.deepcopy(out);bad[key]=value
            with self.assertRaises(RecoveryError):verify_recovery(bad)
        with self.assertRaises(RecoveryError):merge(prior(),{'intangible_capex_krw':0},reason='recovered',archive_hash='a'*64,profile_hash='b'*64)

    def test_profile_identity_and_bounded_original_archive(self):
        ids=company_ids({'status':'000','corp_code':'00123456','stock_code':'005930','bizr_no':'123-45-67890'},'00123456','005930')
        self.assertIn('1234567890',ids)
        with self.assertRaises(RecoveryError):company_ids({'status':'000','corp_code':'00999999','stock_code':'005930'},'00123456','005930')
        values,reason=archive_values(archive(xml()),year=2024,scope='CFS',identifiers=ids,expected_cfo=100)
        self.assertEqual(values['intangible_capex_krw'],0)

    def test_oversized_original_archive_is_skipped_without_relaxing_safety_limit(self):
        from unittest.mock import patch
        import v3_dart_xbrl_cashflow_recovery as recovery
        from v3_dart_xbrl_capex_pilot import XbrlPilotError
        args={'year':2024,'scope':'CFS','identifiers':{'00123456'},'expected_cfo':100}
        with patch.object(recovery,'archive_values',side_effect=XbrlPilotError(
                'xbrl_zip_decompression_bounds_exceeded')):
            values,reason=bounded_archive_values(b'compressed',**args)
        self.assertEqual((values,reason),({},'archive_decompression_limit'))
        marked=merge(prior(),values,reason=reason,archive_hash='a'*64,profile_hash='b'*64)
        self.assertTrue(verify_recovery(marked) is False)
        self.assertIsNone(marked['free_cashflow_krw'])
        for error in ('unsafe_xbrl_archive_member','xbrl_zip_member_count_exceeded'):
            with patch.object(recovery,'archive_values',side_effect=XbrlPilotError(error)):
                with self.assertRaises(XbrlPilotError):bounded_archive_values(b'compressed',**args)

    def test_fast_queue_finishes_issuer_chains_and_skips_same_receipt_review(self):
        s={'records':{'000001':{'years':{'2023':{**prior(),'year':2023},'2024':prior()}},
                      '000002':{'years':{'2024':prior()}}}}
        chosen=targets(s,{'000001','000002'},2)
        self.assertEqual([x[0] for x in chosen],['000002','000001'])
        seeded=targets(s,{'000001','000002'},1,[('000001',2024)])
        self.assertEqual((seeded[0][0],seeded[0][1]),('000001','2024'))
        s['records']['000002']['years']['2024']['original_xbrl_review']={'contract':'x'}
        self.assertTrue(all(x[0]=='000001' for x in targets(s,{'000001','000002'},2)))

    def test_90point_source_candidates_precede_near_100_without_imputing_facts(self):
        s={'records':{'000001':{'years':{'2023':{**prior(),'year':2023},'2024':prior()}},
                      '000002':{'years':{'2024':prior()}}}}
        report={'rows':{
            '000001':{'available':87,'parts':{'financial':{'cashflow_fcf_persistence':[0,0]},
                        'valuation':{'fcf_yield':[0,2.5]}}},
            '000002':{'available':98,'parts':{'financial':{'cashflow_fcf_persistence':[0,0]},
                        'valuation':{'fcf_yield':[0,2.5]}}}}}
        focus=cashflow_90_focus(report,{'000001','000002'})
        self.assertEqual(focus,{'000001'})
        self.assertEqual(targets(s,{'000001','000002'},1,near_90=focus)[0][0],'000001')
        # A seeded structural sample must not displace a sub-90 issuer that
        # could mathematically reach the 90-point observation band.
        self.assertEqual(targets(s,{'000001','000002'},1,
            [('000002',2024)],near_90=focus)[0][0],'000001')
        self.assertIsNone(s['records']['000001']['years']['2024']['free_cashflow_krw'])
        with self.assertRaises(RecoveryError):
            targets(s,{'000002'},1,near_90={'000001'})

    def test_snapshot_verifies_original_provenance_and_formulas(self):
        import v3_dart_cashflow_enrichment as c
        out=merge(prior(),extract(xml()),reason='no_missing_supported_fact',archive_hash='a'*64,profile_hash='b'*64)
        s=c.summary('2026-11-06',{'005930':{}},{'005930':{'scope':'CFS','years':{'2024':out}}},'c'*64)
        self.assertEqual(s['metrics']['original_xbrl_recovered_years'],1)
        self.assertEqual(s['metrics']['fcf_company_years'],1)
        self.assertFalse(s['official_six_pillar_100pt_certified'])
