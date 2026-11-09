import copy
import unittest
import v3_dart_xbrl_parent_equity as m
from test_v3_dart_xbrl_capex_pilot import archive
from test_v3_dart_parent_equity_recovery import data

NS='http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'


def xml(*,total=1000,cfo=100,owner=None,nci=None,column=None,entity='00100001',instant='2025-12-31',scope='ConsolidatedMember',extra=''):
    def ctx(key,period,dimensions):return f'<context id="{key}"><entity><identifier scheme="http://dart.fss.or.kr">{entity}</identifier></entity><period>{period}</period><scenario><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:{scope}</d:explicitMember>{dimensions}</scenario></context>'
    contexts=ctx('CF','<startDate>2025-01-01</startDate><endDate>2025-12-31</endDate>','')+ctx('BS',f'<instant>{instant}</instant>','')
    contexts+=ctx('OWNER',f'<instant>{instant}</instant>','<d:explicitMember dimension="i:ComponentsOfEquityAxis">i:EquityAttributableToOwnersOfParentMember</d:explicitMember>'+extra)
    def fact(name,value,ctx):return f'<i:{name} contextRef="{ctx}" unitRef="W">{value}</i:{name}>'
    facts=fact('CashFlowsFromUsedInOperatingActivities',cfo,'CF')+fact('Equity',total,'BS')
    if owner is not None:facts+=fact('EquityAttributableToOwnersOfParent',owner,'BS')
    if nci is not None:facts+=fact('NoncontrollingInterests',nci,'BS')
    if column is not None:facts+=fact('Equity',column,'OWNER')
    return f'<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:i="{NS}" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217">{contexts}<unit id="W"><measure>iso:KRW</measure></unit>{facts}</xbrl>'.encode()


def extract(raw):return m.extract(archive(raw),identifiers={'00100001'},cfo_anchor=100,equity_anchor=1000)


class OriginalParentEquityTests(unittest.TestCase):
    def test_actual_reported_parent_column_is_not_total_equity(self):
        found=extract(xml(column=800))
        self.assertEqual(found,{'owner':800,'nci':None,'basis':'parent_equity_column'})
        self.assertEqual(extract(xml(owner=800))['basis'],'direct_parent_equity')

    def test_explicit_nci_zero_is_observed_but_absence_is_not_zero(self):
        self.assertEqual(extract(xml(nci=0)),{'owner':1000,'nci':0,'basis':'total_minus_explicit_nci'})
        with self.assertRaisesRegex(m.Blocked,'not_observed'):extract(xml())

    def test_wrong_scope_period_entity_currency_and_extra_dimensions_rejected(self):
        cases=[xml(column=800,scope='SeparateMember'),xml(column=800,instant='2024-12-31'),
            xml(column=800,entity='00999999'),xml(column=800).replace(b'iso:KRW',b'iso:USD'),
            xml(column=800,extra='<d:explicitMember dimension="i:OtherAxis">i:OtherMember</d:explicitMember>')]
        for raw in cases:
            with self.subTest(raw=raw[:20]),self.assertRaises(m.Blocked):extract(raw)

    def test_conflicting_total_cfo_parent_facts_and_nil_are_not_admitted(self):
        cases=[xml(owner=800,total=999),xml(owner=800,cfo=99),xml(owner=800,nci=100),
            xml(owner=800,column=801),xml(column=''),xml(owner=0)]
        for raw in cases:
            with self.subTest(raw=raw[-20:]),self.assertRaises(m.Blocked):extract(raw)

    def test_non_zip_provider_error_is_interruption_not_missing_original(self):
        with self.assertRaises(Exception):m.extract(b'<result><status>020</status></result>',identifiers={'00100001'},cfo_anchor=100,equity_anchor=1000)

    def test_original_proof_replays_existing_book_without_source_mutation(self):
        catalog,book,cash,_,code,annual=data();before=copy.deepcopy(book)
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw'];total=annual['values']['equity']
        raw=archive(xml(total=total,cfo=cfo,column=total-100))
        found=m.extract(raw,identifiers={'00100001'},cfo_anchor=cfo,equity_anchor=total)
        proof=m.proof(code,annual,cfo,found,'a'*64,'b'*64,catalog['date'])
        saved=m.base.blank(catalog['date']);saved['records'][code]=proof
        self.assertEqual(m.base.verify(saved,catalog,book,cash),1)
        result=m.base.overlay(book,catalog,saved,cash)
        self.assertEqual(book,before)
        self.assertIsNotNone(next(r for r in result['observations'] if r['code']==code)['pbr_simple_annual_reference'])
        forged=copy.deepcopy(saved);p=forged['records'][code];p['original_xbrl']['profile_sha256']='bad';p['digest']=m.base.cf.fingerprint({k:v for k,v in p.items() if k!='digest'})
        with self.assertRaises(m.base.ParentEquityRecoveryError):m.base.verify(forged,catalog,book,cash)

    def test_per_issuer_source_rejection_is_distinct_from_provider_outage(self):
        err=m.base.pilot.ParentEquityError('parent_equity_fact_receipt_mismatch')
        self.assertEqual(m.base.rejected_source_reason({'status':'000'},err),'source_identity_or_receipt_rejected')
        with self.assertRaises(m.base.pilot.ParentEquityError):m.base.rejected_source_reason({'status':'020'},err)
        with self.assertRaises(m.base.pilot.ParentEquityError):m.base.rejected_source_reason({'status':'000'},m.base.pilot.ParentEquityError('parent_equity_provider_unavailable'))

    def test_writers_share_lock_and_continue_only_after_progress(self):
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-original-parent-equity.yml').read_text()
        self.assertIn('group: v3-dart-parent-equity-supplement',text)
        self.assertIn("steps.collect.outputs.reviewed != '0'",text)
        self.assertIn('v3-dart-parent-equity-original-reviews.json',text)
        self.assertIn('head_repository.full_name == github.repository',text)
        self.assertNotIn('python -m unittest',text)
