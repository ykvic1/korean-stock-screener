import copy
from collections import Counter
import io
import unittest
import zipfile
import v3_dart_xbrl_eps_recovery as ep
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_reported_eps as eps
import v3_dart_cashflow_enrichment as cf
from v3_eps_explicit_labels import EPSLabelError
from test_v3_dart_xbrl_eps_recovery import xml,missing
from test_v3_dart_reported_eps import BASE,DAY,inputs
from v3_operational_research_score import verified_reported_eps


def packed(raw,*,label='기본주당이익(손실)',item_type='num:perShareItemType',href='company.xsd#custom_eps',schema_extra='',label_extra='',label_prefix=''):
    schema=f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:x="http://www.xbrl.org/2003/instance" xmlns:num="http://www.xbrl.org/dtr/type/numeric" targetNamespace="http://untrusted.example"><xs:element id="custom_eps" name="EarningsPerShare" type="{item_type}" substitutionGroup="x:item" x:periodType="duration"/>{schema_extra}</xs:schema>'''
    labels=f'''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink"><link:labelLink xlink:type="extended"><link:loc xlink:type="locator" xlink:label="concept" xlink:href="{href}"/><link:label xlink:type="resource" xlink:label="text" xml:lang="ko">{label}</link:label><link:labelArc xlink:type="arc" xlink:from="concept" xlink:to="text" xlink:arcrole="http://www.xbrl.org/2003/arcrole/concept-label"/>{label_extra}</link:labelLink></link:linkbase>'''
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('report.xbrl',raw);z.writestr('company.xsd',schema);z.writestr('labels.xml',labels)
    return stream.getvalue()


def extension_xml(**kwargs):
    return xml(**kwargs).replace(b'<i:BasicEarningsLossPerShare',b'<bad:EarningsPerShare').replace(b'</i:BasicEarningsLossPerShare',b'</bad:EarningsPerShare')


def ordinary_xml(*,member='OrdinarySharesMember',axis='ClassesOfShareCapitalAxis',extra='',scope='ConsolidatedMember'):
    raw=xml(member=scope)
    contexts=b''.join(b'<context'+raw.split(b'<context')[i].split(b'</context>')[0]+b'</context>' for i in range(1,4))
    dims=f'<d:explicitMember dimension="i:{axis}">i:{member}</d:explicitMember>{extra}'.encode()
    contexts=contexts.replace(b'id="C',b'id="E').replace(b'</scenario>',dims+b'</scenario>')
    return raw.replace(b'<unit id="U">',contexts+b'<unit id="U">').replace(b'<i:BasicEarningsLossPerShare contextRef="C',b'<i:BasicEarningsLossPerShare contextRef="E')


def extract(raw):
    binding={};diag=Counter()
    values,reason=ep.extract_archive(raw,scope='CFS',identifiers={'00123456'},cfo_anchor=100,extended=True,binding_result=binding,diagnostic=diag)
    return values,reason,binding,diag


def reviewed():
    return ep.merge(missing(),{},reason='no_additional_reported_basic_eps',cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)


def adopt(row,values,binding):
    return ep.merge(ep.prepare_context_recheck(row),values,reason='no_additional_reported_basic_eps',cfo_anchor=100,
        archive_hash='a'*64,profile_hash='b'*64,binding_hash=binding['sha256'])


class ExplicitEPSContextRecoveryTests(unittest.TestCase):
    def test_literal_extension_label_schema_type_unit_and_scope_recover_reported_values(self):
        values,_,binding,diag=extract(packed(extension_xml()))
        self.assertEqual(values,{'2023':-20.,'2024':0.,'2025':150.25})
        self.assertEqual(diag['literal_extension_basic_eps_facts'],3)
        row=adopt(reviewed(),values,binding);self.assertTrue(ep.verify_review(row))
        self.assertEqual(row['source_account_id'],eps.ORIGINAL_REPORTED_BASIC_EPS_ID)
        c,b=inputs();state=eps.snapshot(DAY,c,b,{BASE['code']:BASE},{BASE['code']:row});eps.plan(c,b,state)
        self.assertIn(BASE['code'],verified_reported_eps(c,b,state))
        self.assertFalse(state['investment_score_certified'])

    def test_exact_joint_basic_diluted_and_whitespace_labels_are_supported(self):
        for label in ('기본 및 희석 주당이익(손실)','기본 주당순이익'):
            self.assertEqual(len(extract(packed(extension_xml(),label=label))[0]),3)

    def test_diluted_only_generic_similar_labels_and_non_per_share_schema_types_not_accepted(self):
        for label in ('희석주당이익(손실)','주당이익','예상기본주당이익','계속영업기본주당이익'):
            self.assertEqual(extract(packed(extension_xml(),label=label))[0],{})
        for item_type in ('x:monetaryItemType','xs:string','x:perShareItemType'):
            self.assertEqual(extract(packed(extension_xml(),item_type=item_type))[0],{})

    def test_external_or_wrong_schema_locator_cannot_authorize_a_label(self):
        for href in ('https://example.com/company.xsd#custom_eps','../company.xsd#custom_eps','company.xsd#other'):
            self.assertEqual(extract(packed(extension_xml(),href=href))[0],{})

    def test_wrong_issuer_scope_period_and_unit_never_recover_extension_eps(self):
        for kw in ({'identity':'00999999'},{'member':'SeparateMember'},{'den':'bad:shares'}):
            self.assertEqual(extract(packed(extension_xml(**kw)))[0],{})
        values=extract(packed(extension_xml().replace(b'2023-12-31',b'2023-09-30')))[0]
        self.assertNotIn('2023',values)

    def test_explicit_ordinary_shares_work_only_when_scope_is_explicit_and_cfo_stays_scope_only(self):
        raw=ordinary_xml();values,_,binding,_=extract(packed(raw));self.assertEqual(len(values),3)
        # Existing consumers and default EPS extraction keep the old context contract.
        self.assertEqual(ep.extract_archive(packed(raw),scope='CFS',identifiers={'00123456'},cfo_anchor=100)[0],{})
        _,_,ctx=original.verified_contexts(raw,year=2025,scope='CFS',identifiers={'00123456'})
        self.assertFalse(ctx['E2025']);self.assertTrue(ctx['C2025'])
        self.assertTrue(ep.verify_review(adopt(reviewed(),values,binding)))

    def test_preference_custom_members_other_axes_and_extra_dimensions_remain_excluded(self):
        for kw in ({'member':'PreferenceSharesMember'},{'member':'ClassAMember'}, {'axis':'OtherAxis'},
            {'extra':'<d:explicitMember dimension="i:OtherAxis">i:OtherMember</d:explicitMember>'}):
            self.assertEqual(extract(packed(ordinary_xml(**kw)))[0],{})
        with self.assertRaises(original.RecoveryError):original.verified_contexts(xml(),year=2025,scope='CFS',identifiers={'00123456'},ordinary_share=1)
        with self.assertRaises(original.RecoveryError):original.verified_contexts(xml(),year=2025,scope='CFS',identifiers={'00123456'},ordinary_share=True,equity_owner=True)

    def test_registered_ordinary_share_axis_with_exact_standard_ordinary_member(self):
        self.assertEqual(len(extract(packed(ordinary_xml(axis='ClassesOfOrdinarySharesAxis')))[0]),3)
        self.assertEqual(extract(packed(ordinary_xml(axis='ClassesOfOrdinarySharesAxis',member='ClassAMember')))[0],{})

    def test_conflicting_original_standard_extension_or_saved_eps_never_overwrite(self):
        raw=xml().replace(b'</xbrl>',b'<bad:EarningsPerShare contextRef="C2025" unitRef="E">151</bad:EarningsPerShare></xbrl>')
        with self.assertRaises(ep.OriginalEPSError):extract(packed(raw))
        row=reviewed();row['reported_basic_eps_2023_2025']=[None,None,151.]
        row['status']='reported_basic_eps';row['source_account_id']='ifrs-full_BasicEarningsLossPerShare';row['metrics']=eps.metrics(row['reported_basic_eps_2023_2025'])
        values,_,binding,_=extract(packed(extension_xml()))
        with self.assertRaises(ep.OriginalEPSError):adopt(row,values,binding)

    def test_duplicate_schema_or_label_locators_fail_closed(self):
        extra='<xs:element id="custom_eps" name="Other" type="num:perShareItemType" substitutionGroup="x:item" x:periodType="duration"/>'
        with self.assertRaises(EPSLabelError):extract(packed(extension_xml(),schema_extra=extra))
        extra='<link:loc xlink:type="locator" xlink:label="concept" xlink:href="company.xsd#missing"/>'
        with self.assertRaises(EPSLabelError):extract(packed(extension_xml(),label_extra=extra))

    def test_previous_review_is_preserved_and_second_recheck_or_positive_proof_not_overwritten(self):
        old=reviewed();before=copy.deepcopy(old);values,_,binding,_=extract(packed(extension_xml()))
        row=adopt(old,values,binding)
        self.assertEqual(old,before)
        self.assertEqual(row[ep.CONTEXT_REVIEW]['previous_review'],before['original_xbrl_review'])
        with self.assertRaises(ep.OriginalEPSError):ep.prepare_context_recheck(row)
        legacy=ep.merge(missing(),{'2025':1.},reason='recovered',cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)
        with self.assertRaises(ep.OriginalEPSError):ep.prepare_context_recheck(legacy)

    def test_negative_recheck_preserves_missing_values_and_is_not_queued_again(self):
        old=reviewed();row=adopt(old,{}, {'sha256':'a'*64});self.assertFalse(ep.verify_review(row))
        self.assertEqual(row['reported_basic_eps_2023_2025'],[None]*3)
        anchor={'major_annual_receipt_matched':True,'scope':'CFS','annual_receipt_id':row['annual_receipt_id'],'operating_cashflow_krw':100}
        cash={'records':{row['code']:{'years':{'2025':anchor}}}}
        self.assertEqual(len(ep.recovery_candidates({old['code']:old},cash,{})),1)
        self.assertEqual(ep.recovery_candidates({row['code']:row},cash,{}),[])

    def test_new_proofs_require_context_contract_and_binding_digest_legacy_still_valid(self):
        values,_,binding,_=extract(packed(extension_xml()));row=adopt(reviewed(),values,binding)
        for key,value in (('context_contract','wrong'),('label_binding_sha256','bad')):
            bad=copy.deepcopy(row);bad['original_xbrl_proof'][key]=value
            bad['original_xbrl_proof']['digest']=cf.fingerprint({k:v for k,v in bad['original_xbrl_proof'].items() if k!='digest'})
            with self.assertRaises(ep.OriginalEPSError):ep.verify_review(bad)
        bad=copy.deepcopy(row);bad[ep.CONTEXT_REVIEW]['previous_review']['reason']='recovered'
        with self.assertRaises(ep.OriginalEPSError):ep.verify_review(bad)
        legacy=ep.merge(missing(),{'2025':1.},reason='recovered',cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)
        self.assertTrue(ep.verify_review(legacy))

    def test_v2_source_id_without_its_proof_cannot_enter_reported_eps_state(self):
        row=missing();row['source_account_id']=eps.ORIGINAL_REPORTED_BASIC_EPS_ID
        row['reported_basic_eps_2023_2025']=[1.,2.,3.];row['status']='reported_basic_eps';row['metrics']=eps.metrics([1.,2.,3.])
        c,b=inputs();state=eps.snapshot(DAY,c,b,{BASE['code']:BASE},{BASE['code']:row})
        with self.assertRaises(eps.EPSError):eps.plan(c,b,state)


if __name__=='__main__':unittest.main()
