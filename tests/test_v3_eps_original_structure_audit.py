import copy
import json
from pathlib import Path
import unittest
import v3_eps_original_structure_audit as a
import v3_dart_xbrl_eps_recovery as ep
from test_v3_dart_xbrl_eps_recovery import xml, missing
from test_v3_dart_xbrl_capex_pilot import archive


def inspect(raw):
    return a.inspect_archive(archive(raw),scope='CFS',identifiers={'00123456'},cfo_anchor=100)


class EPSStructureResearchTests(unittest.TestCase):
    def test_standard_scoped_basic_eps_counts_do_not_retain_values_or_identity(self):
        counts=inspect(xml(values=('1234567','7654321','2468135')))
        self.assertEqual(counts['standard_basic_facts'],3)
        self.assertEqual(counts['standard_basic_scope_only'],3)
        self.assertEqual(counts['standard_basic_per_share_unit'],3)
        text=json.dumps(counts)
        for private in ('00123456','1234567','7654321','2468135','C2025'):
            self.assertNotIn(private,text)

    def test_diluted_other_standard_extension_and_wrong_units_are_distinct_hints(self):
        self.assertEqual(inspect(xml(concept='DilutedEarningsLossPerShare'))['standard_diluted_facts'],3)
        self.assertEqual(inspect(xml(concept='BasicEarningsLossPerShareFromContinuingOperations'))['other_standard_eps_named_facts'],3)
        # CFO keeps the verified namespace; only EPS facts are extension hints.
        raw=xml().replace(b'<i:BasicEarningsLossPerShare',b'<bad:BasicEarningsLossPerShare').replace(b'</i:BasicEarningsLossPerShare',b'</bad:BasicEarningsLossPerShare')
        self.assertEqual(inspect(raw)['extension_eps_named_facts'],3)
        self.assertEqual(inspect(xml(den='bad:shares'))['standard_basic_other_unit'],3)

    def test_additional_share_class_context_is_diagnosed_but_never_accepted(self):
        raw=xml()
        contexts=b''.join(raw.split(b'<context')[i].split(b'</context>')[0].join((b'<context',b'</context>')) for i in range(1,4))
        extra=contexts.replace(b'id="C',b'id="E').replace(b'</scenario>',b'<d:explicitMember dimension="i:ClassesOfShareCapitalAxis">i:OrdinarySharesMember</d:explicitMember></scenario>')
        raw=raw.replace(b'<unit id="U">',extra+b'<unit id="U">')
        raw=raw.replace(b'<i:BasicEarningsLossPerShare contextRef="C',b'<i:BasicEarningsLossPerShare contextRef="E')
        self.assertEqual(inspect(raw)['standard_basic_scope_with_share_class_axis'],3)
        self.assertEqual(ep.extract_archive(archive(raw),scope='CFS',identifiers={'00123456'},cfo_anchor=100)[0],{})

    def test_wrong_identity_scope_cfo_and_unsafe_xml_never_produce_anchored_hints(self):
        for changes in ({'identity':'00999999'},{'member':'SeparateMember'}):
            self.assertNotIn('cfo_anchored_instances',inspect(xml(**changes)))
        with self.assertRaises(ep.OriginalEPSError):inspect(xml(cfo='101'))
        with self.assertRaises(a.xp.XbrlPilotError):inspect(xml().replace(b'<xbrl',b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><xbrl',1))

    def test_only_exact_annual_periods_are_counted(self):
        counts=inspect(xml().replace(b'2023-12-31',b'2023-09-30'))
        self.assertEqual(counts['standard_basic_facts'],2)
        self.assertNotIn('standard_basic_year_2023',counts)

    def test_invalid_eps_values_are_only_diagnosed_after_cfo_anchor(self):
        counts=inspect(xml(values=('1','NaN','3')))
        self.assertEqual(counts['unusable_standard_eps_instances'],1)
        self.assertEqual(counts['cfo_anchored_instances'],1)
        self.assertNotIn('NaN',json.dumps(counts))

    def test_reviewed_missing_queue_uses_exact_receipt_and_scope_without_mutation(self):
        row=ep.merge(missing(),{},reason='no_additional_reported_basic_eps',cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)
        code=row['code'];cash={'records':{code:{'years':{'2025':{'scope':'CFS',
            'annual_receipt_id':row['annual_receipt_id'],'operating_cashflow_krw':100,'major_annual_receipt_matched':True}}}}}
        before=copy.deepcopy(row)
        self.assertEqual(a.candidates({code:row},cash),[(code,row,100)])
        self.assertEqual(row,before)
        cash['records'][code]['years']['2025']['scope']='OFS'
        self.assertEqual(a.candidates({code:row},cash),[])
        self.assertEqual(a.candidates({code:missing()},cash),[])

    def test_workflow_has_no_source_writer_or_score_publication(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-eps-original-structure-audit.yml').read_text()
        for forbidden in ('contents: write','git push','/dispatches','schedule:'):
            self.assertNotIn(forbidden,text)
        self.assertIn('--limit 4',text)
        self.assertIn('retention-days: 14',text)


if __name__=='__main__':unittest.main()
