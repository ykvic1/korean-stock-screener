"""Offline XBRL source-vs-API numeric tests: no network or real credentials."""
import json
import os
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import patch
from zipfile import ZipFile

from official_probe.__main__ import run
from official_probe.clients import DARTClient, ProbeError
from official_probe.xbrl_compare import (
    _instance_from_zip, compare_annual_xbrl, _compare_metric,
)

CORP = '01199727'
ASOF = '2026-10-16'
RECEIPT = '20260316001120'
NS = 'http://www.xbrl.org/2003/instance'
IFRS = 'http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'
DART = 'http://dart.fss.or.kr/taxonomy/2024-03-27/dart'


def zip_xml(xml=None, name='annual.xbrl', add_other=False):
    if xml is None:
        xml = xbrl()
    b = BytesIO()
    with ZipFile(b, 'w') as z:
        z.writestr(name, xml)
        if add_other:
            z.writestr('labels.xml', '<root/>')
    return b.getvalue()


def xbrl(*, revenue=1000, year='2025', dimensioned=False, dup=False,
         period_end=None, unit='iso4217:KRW'):
    start = year + '-01-01'
    end = period_end or (year + '-12-31')
    seg = '<xbrli:segment><test:dimension>OFS</test:dimension></xbrli:segment>' if dimensioned else ''
    extra = ('<ifrs:Revenue contextRef="D" unitRef="U">999</ifrs:Revenue>'
             if dup else '')
    return f'''<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="{NS}" xmlns:ifrs="{IFRS}" xmlns:dart="{DART}"
            xmlns:test="http://example.com/test">
  <xbrli:context id="D">
    <xbrli:entity><xbrli:identifier scheme="test">01199727</xbrli:identifier>{seg}</xbrli:entity>
    <xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="I">
    <xbrli:entity><xbrli:identifier scheme="test">01199727</xbrli:identifier>{seg}</xbrli:entity>
    <xbrli:period><xbrli:instant>{end}</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="U"><xbrli:measure>{unit}</xbrli:measure></xbrli:unit>
  <ifrs:Revenue contextRef="D" unitRef="U" decimals="0">{revenue}</ifrs:Revenue>
  {extra}
  <dart:OperatingIncomeLoss contextRef="D" unitRef="U" decimals="0">200</dart:OperatingIncomeLoss>
  <ifrs:CashFlowsFromUsedInOperatingActivities contextRef="D" unitRef="U" decimals="0">150</ifrs:CashFlowsFromUsedInOperatingActivities>
  <ifrs:Assets contextRef="I" unitRef="U" decimals="0">8000</ifrs:Assets>
  <ifrs:Liabilities contextRef="I" unitRef="U" decimals="0">3200</ifrs:Liabilities>
  <ifrs:Equity contextRef="I" unitRef="U" decimals="0">4800</ifrs:Equity>
</xbrli:xbrl>'''.encode('utf-8')


def finrow(aid, sj_div, amount, receipt=RECEIPT, **extra):
    row = {'corp_code': CORP, 'rcept_no': receipt,
           'bsns_year': '2025', 'reprt_code': '11011', 'fs_div': 'OFS',
           'account_id': aid, 'sj_div': sj_div,
           'currency': 'KRW', 'account_detail': '-',
           'thstrm_amount': str(amount)}
    row.update(extra)
    return row


def api_rows(receipt=RECEIPT):
    return [
        finrow('ifrs-full_Revenue', 'IS', 1000, receipt),
        finrow('dart_OperatingIncomeLoss', 'IS', 200, receipt),
        finrow('ifrs-full_CashFlowsFromUsedInOperatingActivities', 'CF', 150, receipt),
        finrow('ifrs-full_Assets', 'BS', 8000, receipt),
        finrow('ifrs-full_Liabilities', 'BS', 3200, receipt),
        finrow('ifrs-full_Equity', 'BS', 4800, receipt),
    ]


def filing(receipt=RECEIPT, report='사업보고서 (2025.12)'):
    return {'rcept_no': receipt, 'rcept_dt': receipt[:8],
            'corp_code': CORP, 'report_nm': report}


class FakeDART:
    def __init__(self, zip_data=None, *, rows=None, filings=None):
        self.zip_data = zip_data or zip_xml()
        self.rows = api_rows() if rows is None else rows
        self.filings = ([filing()] if filings is None else filings)
        self.api_calls = []
        self.xbrl_calls = []

    def _api(self, endpoint, **params):
        self.api_calls.append((endpoint, params))
        if endpoint == 'list.json':
            return {'status':'000', 'total_page':1, 'list':self.filings}
        if endpoint == 'fnlttSinglAcntAll.json':
            return {'status':'000', 'list':self.rows}
        raise AssertionError('Unexpected API endpoint')

    def original_xbrl_zip(self, receipt, code):
        self.xbrl_calls.append((receipt,code))
        return self.zip_data


class XbrlNumericTests(unittest.TestCase):
    def test_exact_six_amounts_equal_but_never_pit_or_scope_validated(self):
        c = FakeDART(zip_xml(add_other=True))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT, 'OFS')
        self.assertEqual(out['status'],'six_values_observed_equal_scope_unverified')
        self.assertEqual(out['equal_observed_count'],6)
        self.assertEqual(out['different_observed_count'],0)
        self.assertTrue(out['all_six_values_equal_as_observed'])
        self.assertTrue(out['financial_api_receipt_matches'])
        self.assertTrue(out['xbrl_original_archive_observed'])
        self.assertEqual(out['metrics']['revenue']['xbrl_amount_krw'],1000)
        self.assertEqual(out['metrics']['revenue']['api_amount_krw'],1000)
        self.assertEqual(len(c.xbrl_calls),1)
        self.assertEqual(c.xbrl_calls[0],(RECEIPT,'11011'))
        self.assertEqual(len(c.api_calls),2)
        self.assertFalse(out['xbrl_accounting_scope_verified'])
        self.assertFalse(out['historical_numeric_snapshot_verified'])
        self.assertFalse(out['confirmed_scoring_allowed'])
        self.assertFalse(out['investment_data_ready'])

    def test_numeric_difference_is_not_silently_matched(self):
        c = FakeDART(zip_xml(xbrl(revenue=1200)))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['status'], 'partial_or_different_values_unverified')
        self.assertEqual(out['equal_observed_count'], 5)
        self.assertEqual(out['different_observed_count'], 1)
        self.assertEqual(out['metrics']['revenue']['status'],
                         'different_observed_scope_unverified')
        self.assertFalse(out['confirmed_scoring_allowed'])

    def test_multiple_revenue_contexts_are_ambiguous(self):
        c = FakeDART(zip_xml(xbrl(dup=True)))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['metrics']['revenue']['status'], 'xbrl_fact_or_scope_ambiguous')
        self.assertIsNone(out['metrics']['revenue']['xbrl_amount_krw'])
        evidence = out['metrics']['revenue']
        self.assertEqual(evidence['candidate_fact_count'], 2)
        self.assertEqual(evidence['xbrl_dimensioned_fact_count'], 0)
        self.assertEqual(evidence['xbrl_undimensioned_fact_count'], 2)
        self.assertEqual(evidence['xbrl_distinct_context_count'], 1)
        self.assertIn('multiple_candidate_facts', evidence['xbrl_ambiguity_reasons'])
        self.assertFalse(evidence['xbrl_accounting_scope_verified'])

    def test_dimensioned_primary_context_cannot_be_assigned_OFS(self):
        c = FakeDART(zip_xml(xbrl(dimensioned=True)))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['metrics']['revenue']['status'], 'xbrl_fact_or_scope_ambiguous')
        evidence = out['metrics']['revenue']
        self.assertEqual(evidence['candidate_fact_count'], 1)
        self.assertEqual(evidence['xbrl_dimensioned_fact_count'], 1)
        self.assertEqual(evidence['xbrl_undimensioned_fact_count'], 0)
        self.assertEqual(evidence['xbrl_distinct_context_count'], 1)
        self.assertIn('segment_or_scenario_present', evidence['xbrl_ambiguity_reasons'])
        self.assertIsNone(evidence['xbrl_amount_krw'])
        self.assertFalse(evidence['historical_numeric_snapshot_verified'])

    def test_explicit_dimension_axis_is_diagnostic_not_scope_validation(self):
        xml = xbrl(dimensioned=True).replace(
            b'xmlns:test="http://example.com/test"',
            b'xmlns:test="http://example.com/test" '
            b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"').replace(
            b'<test:dimension>OFS</test:dimension>',
            b'<xbrldi:explicitMember dimension="test:FinancialStatementsAxis">'
            b'test:SeparateMember</xbrldi:explicitMember>')
        c = FakeDART(zip_xml(xml))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        metric = out['metrics']['revenue']
        self.assertEqual(metric['status'], 'xbrl_fact_or_scope_ambiguous')
        self.assertEqual(metric['xbrl_candidate_api_amount_equal_count'], 1)
        self.assertEqual(metric['xbrl_unparsed_dimensioned_fact_count'], 0)
        self.assertEqual(metric['xbrl_dimension_axis_samples'], [{
            'kind': 'explicit', 'axis_local': 'FinancialStatementsAxis',
            'member_local': 'SeparateMember', 'candidate_fact_count': 1}])
        self.assertIsNone(metric['xbrl_amount_krw'])
        self.assertFalse(out['confirmed_scoring_allowed'])
        self.assertFalse(out['xbrl_accounting_scope_verified'])
        self.assertFalse(out['investment_data_ready'])



    def test_two_revenue_taxonomy_concepts_remain_unverified_with_one_api_match(self):
        xml = xbrl(dimensioned=True).replace(
            b'xmlns:test="http://example.com/test"',
            b'xmlns:test="http://example.com/test" '
            b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"').replace(
            b'<test:dimension>OFS</test:dimension>',
            b'<xbrldi:explicitMember dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            b'test:SeparateMember</xbrldi:explicitMember>')
        xml = xml.replace(
            b'  <dart:OperatingIncomeLoss',
            b'  <ifrs:RevenueFromContractsWithCustomers contextRef="D" unitRef="U" '
            b'decimals="0">999</ifrs:RevenueFromContractsWithCustomers>\n'
            b'  <dart:OperatingIncomeLoss')
        out = compare_annual_xbrl(FakeDART(zip_xml(xml)), CORP, 2025, ASOF, RECEIPT)
        m = out['metrics']['revenue']
        self.assertEqual(m['xbrl_requested_scope_pattern_only_candidate_count'], 2)
        self.assertEqual(m['xbrl_requested_scope_pattern_only_api_equal_count'], 1)
        self.assertEqual(m['xbrl_scope_pattern_duplicate_kind'],
                         'multiple_taxonomy_concepts_or_namespaces')
        self.assertEqual({g['concept_local'] for g in m['xbrl_scope_pattern_concept_groups']},
                         {'Revenue', 'RevenueFromContractsWithCustomers'})
        self.assertEqual(sorted(g['api_amount_equal_count']
                                for g in m['xbrl_scope_pattern_concept_groups']), [0, 1])
        self.assertEqual(sorted(g['fact_count']
                                for g in m['xbrl_scope_pattern_concept_groups']), [1, 1])
        for group in m['xbrl_scope_pattern_concept_groups']:
            self.assertEqual(len(group['namespace_sha256_prefix']), 16)
            self.assertFalse(group.get('amount_krw'))
        self.assertFalse(m['xbrl_taxonomy_concept_meaning_verified'])
        self.assertFalse(m['xbrl_presentation_statement_role_verified'])
        self.assertEqual(m['status'], 'xbrl_fact_or_scope_ambiguous')
        self.assertIsNone(m['xbrl_amount_krw'])
        self.assertFalse(out['confirmed_scoring_allowed'])

    def test_duplicate_same_concept_same_context_does_not_become_statement_fact(self):
        xml = xbrl(dimensioned=True, dup=True).replace(
            b'xmlns:test="http://example.com/test"',
            b'xmlns:test="http://example.com/test" '
            b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"').replace(
            b'<test:dimension>OFS</test:dimension>',
            b'<xbrldi:explicitMember dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            b'test:SeparateMember</xbrldi:explicitMember>')
        result = compare_annual_xbrl(FakeDART(zip_xml(xml)), CORP, 2025, ASOF, RECEIPT)
        revenue = result['metrics']['revenue']
        self.assertEqual(revenue['xbrl_scope_pattern_duplicate_kind'],
                         'same_concept_same_context_multiple_facts')
        self.assertEqual(len(revenue['xbrl_scope_pattern_concept_groups']), 1)
        self.assertEqual(revenue['xbrl_scope_pattern_concept_groups'][0]['fact_count'], 2)
        self.assertEqual(revenue['xbrl_scope_pattern_concept_groups'][0]['distinct_context_count'], 1)
        self.assertEqual(revenue['xbrl_scope_pattern_concept_groups'][0]['distinct_value_count'], 2)
        self.assertFalse(result['investment_data_ready'])

    def test_bundled_presentation_arc_is_a_hint_not_a_primary_statement_proof(self):
        xml = xbrl()
        linkbase = b'''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
              xmlns:xlink="http://www.w3.org/1999/xlink">
          <link:presentationLink xlink:role="urn:example:note-not-statement">
            <link:loc xlink:label="r" xlink:href="taxonomy.xsd#ifrs-full_Revenue"/>
            <link:loc xlink:label="a" xlink:href="taxonomy.xsd#ifrs-full_ProfitLoss"/>
            <link:presentationArc xlink:from="a" xlink:to="r"/>
          </link:presentationLink>
        </link:linkbase>'''
        buffer = BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('annual.xbrl', xml)
            archive.writestr('custom-presentation.xml', linkbase)
        out = compare_annual_xbrl(
            FakeDART(buffer.getvalue()), CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['embedded_presentation_files_observed'], 1)
        self.assertEqual(out['embedded_presentation_links_observed'], 1)
        self.assertEqual(out['revenue_embedded_presentation_arc_hints']['Revenue'], 1)
        self.assertEqual(out['revenue_embedded_presentation_arc_hints']['RevenueFromContractsWithCustomers'], 0)
        self.assertFalse(out['presentation_linkbase_complete_dts_verified'])
        self.assertFalse(out['primary_statement_role_verified'])
        self.assertFalse(out['investment_data_ready'])

    def test_missing_bundled_presentation_does_not_infer_missing_external_roles(self):
        out = compare_annual_xbrl(FakeDART(zip_xml()), CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['embedded_presentation_files_observed'], 0)
        self.assertEqual(out['revenue_embedded_presentation_arc_hints']['Revenue'], 0)
        self.assertFalse(out['presentation_linkbase_complete_dts_verified'])
        self.assertFalse(out['confirmed_scoring_allowed'])


    def test_presentation_roles_are_only_hints_not_statement_proof(self):
        xml = b'''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
             xmlns:xlink="http://www.w3.org/1999/xlink">
          <link:presentationLink xlink:role="https://example.org/role/IncomeStatement">
            <link:loc xlink:label="r" xlink:href="tax.xsd#ifrs-full_Revenue"/>
            <link:loc xlink:label="root" xlink:href="tax.xsd#ifrs-full_ProfitLoss"/>
            <link:presentationArc xlink:from="root" xlink:to="r"/>
          </link:presentationLink>
          <link:presentationLink xlink:role="https://example.org/role/RevenueNotes">
            <link:loc xlink:label="n" xlink:href="tax.xsd#ifrs-full_RevenueFromContractsWithCustomers"/>
            <link:loc xlink:label="root" xlink:href="tax.xsd#ifrs-full_ProfitLoss"/>
            <link:presentationArc xlink:from="root" xlink:to="n"/>
          </link:presentationLink>
        </link:linkbase>'''
        buf = BytesIO()
        with ZipFile(buf, 'w') as archive:
            archive.writestr('annual.xbrl', xbrl())
            archive.writestr('presentation.xml', xml)
        out = compare_annual_xbrl(FakeDART(buf.getvalue()), CORP, 2025, ASOF, RECEIPT)
        groups = out['revenue_embedded_presentation_role_samples']
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {(x['role_uri_tail'], x['concept_local'], x['presentation_arc_count'])
             for x in groups},
            {('IncomeStatement', 'Revenue', 1),
             ('RevenueNotes', 'RevenueFromContractsWithCustomers', 1)})
        self.assertTrue(out['presentation_role_names_are_only_clues'])
        self.assertFalse(out['primary_statement_role_verified'])
        self.assertFalse(out['presentation_linkbase_complete_dts_verified'])
        self.assertFalse(out['xbrl_accounting_scope_verified'])
        self.assertFalse(out['historical_numeric_snapshot_verified'])
        self.assertFalse(out['confirmed_scoring_allowed'])
        self.assertTrue(all(len(x['role_uri_sha256_prefix']) == 16 for x in groups))

    def test_filing_date_is_not_an_authenticated_historical_numeric_snapshot(self):
        out = compare_annual_xbrl(FakeDART(), CORP, 2025, ASOF, RECEIPT)
        self.assertTrue(out['original_receipt_precedes_asof_date_only'])
        self.assertEqual(out['filing_availability_granularity'], 'calendar_date_only')
        self.assertFalse(out['financial_api_is_historical_snapshot_verified'])
        self.assertFalse(out['asof_join_validated'])
        self.assertFalse(out['historical_numeric_snapshot_verified'])
        self.assertFalse(out['confirmed_scoring_allowed'])

    def test_amendment_prevents_date_only_approval_and_original_zip_fetch(self):
        amended = '20260401000234'
        f = FakeDART(filings=[
            filing(RECEIPT), filing(amended, report='[정정]사업보고서 (2025.12)')
        ])
        out = compare_annual_xbrl(f, CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['status'], 'revision_history_blocks_single_report_comparison')
        self.assertFalse(out['original_receipt_precedes_asof_date_only'])
        self.assertFalse(out['confirmed_scoring_allowed'])
        self.assertEqual(f.xbrl_calls, [])


    def test_embedded_xsd_role_definitions_join_presentation_graph_but_not_certify(self):
        xsd = b'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
            xmlns:link="http://www.xbrl.org/2003/linkbase">
          <xs:annotation><xs:appinfo>
            <link:roleType roleURI="https://example.org/role/SeparateIncomeStatement">
              <link:definition>Separate Income Statement</link:definition>
              <link:usedOn>link:presentationLink</link:usedOn>
            </link:roleType>
            <link:roleType roleURI="https://example.org/role/RevenueNotes">
              <link:definition>Disclosure notes about revenue</link:definition>
              <link:usedOn>link:presentationLink</link:usedOn>
            </link:roleType>
          </xs:appinfo></xs:annotation>
        </xs:schema>'''
        presentation = b'''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink">
          <link:presentationLink xlink:role="https://example.org/role/SeparateIncomeStatement">
            <link:loc xlink:label="parent" xlink:href="tax.xsd#ifrs-full_ProfitLoss"/>
            <link:loc xlink:label="r" xlink:href="tax.xsd#ifrs-full_Revenue"/>
            <link:presentationArc xlink:from="parent" xlink:to="r"/>
          </link:presentationLink>
          <link:presentationLink xlink:role="https://example.org/role/RevenueNotes">
            <link:loc xlink:label="parent" xlink:href="tax.xsd#ifrs-full_DisclosureOfRevenue"/>
            <link:loc xlink:label="r" xlink:href="tax.xsd#ifrs-full_RevenueFromContractsWithCustomers"/>
            <link:presentationArc xlink:from="parent" xlink:to="r"/>
          </link:presentationLink>
        </link:linkbase>'''
        zip_bytes = BytesIO()
        with ZipFile(zip_bytes, 'w') as z:
            z.writestr('instance.xbrl', xbrl())
            z.writestr('extension.xsd', xsd)
            z.writestr('presentation.xml', presentation)
        report = compare_annual_xbrl(FakeDART(zip_bytes.getvalue()),
                                     CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(report['embedded_xsd_file_count'], 1)
        self.assertEqual(report['embedded_role_type_count'], 2)
        roles = {x['concept_local']: x
                 for x in report['revenue_embedded_presentation_role_samples']}
        self.assertEqual(set(roles), {'Revenue','RevenueFromContractsWithCustomers'})
        self.assertTrue(all(v['embedded_role_type_found'] for v in roles.values()))
        self.assertTrue(all(v['embedded_role_type_used_on_presentation']
                            for v in roles.values()))
        self.assertIn('income_statement',
                      roles['Revenue']['embedded_role_definition_hints_unverified'])
        self.assertIn('notes',
                      roles['RevenueFromContractsWithCustomers']['embedded_role_definition_hints_unverified'])
        self.assertEqual(roles['Revenue']['direct_parent_concept_local_samples'],
                         ['ProfitLoss'])
        self.assertEqual(roles['RevenueFromContractsWithCustomers']['direct_parent_concept_local_samples'],
                         ['DisclosureOfRevenue'])
        self.assertEqual(roles['Revenue']['as_child_arc_count'], 1)
        self.assertEqual(roles['Revenue']['as_parent_arc_count'], 0)
        self.assertFalse(report['embedded_role_type_definitions_complete_dts_verified'])
        self.assertFalse(report['primary_statement_role_verified'])
        self.assertFalse(report['xbrl_accounting_scope_verified'])
        self.assertFalse(report['historical_numeric_snapshot_verified'])
        self.assertFalse(report['confirmed_scoring_allowed'])
        self.assertFalse(report['investment_data_ready'])
        self.assertNotIn('Separate Income Statement', json.dumps(report))

    def test_missing_schema_is_unknown_not_statement_denial(self):
        presentation = b'''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink">
          <link:presentationLink xlink:role="https://example.org/role/IncomeStatement">
            <link:loc xlink:label="p" xlink:href="tax.xsd#ifrs-full_ProfitLoss"/>
            <link:loc xlink:label="r" xlink:href="tax.xsd#ifrs-full_Revenue"/>
            <link:presentationArc xlink:from="p" xlink:to="r"/>
          </link:presentationLink>
        </link:linkbase>'''
        zbytes = BytesIO()
        with ZipFile(zbytes,'w') as z:
            z.writestr('instance.xbrl',xbrl())
            z.writestr('presentation.xml',presentation)
        result = _instance_from_zip(zbytes.getvalue())
        roles = result['revenue_presentation_role_samples']
        self.assertEqual(result['embedded_xsd_file_count'],0)
        self.assertFalse(roles[0]['embedded_role_type_found'])
        self.assertFalse(roles[0]['embedded_role_type_used_on_presentation'])
        self.assertFalse(result['embedded_role_type_definitions_complete_dts_verified'])

    def test_conflicting_embedded_role_type_definitions_fail_closed(self):
        prefix = b'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
            xmlns:link="http://www.xbrl.org/2003/linkbase"><xs:annotation>
            <xs:appinfo><link:roleType roleURI="https://example.org/role/X">
            <link:definition>'''
        suffix = b'''</link:definition><link:usedOn>link:presentationLink</link:usedOn>
            </link:roleType></xs:appinfo></xs:annotation></xs:schema>'''
        zbytes = BytesIO()
        with ZipFile(zbytes,'w') as z:
            z.writestr('instance.xbrl',xbrl())
            z.writestr('a.xsd',prefix + b'Income Statement' + suffix)
            z.writestr('b.xsd',prefix + b'Note Disclosure' + suffix)
        with self.assertRaises(ProbeError):
            _instance_from_zip(zbytes.getvalue())

    def test_xsd_document_type_and_unbounded_schema_count_fail_closed(self):
        raw = BytesIO()
        with ZipFile(raw,'w') as z:
            z.writestr('instance.xbrl',xbrl())
            for i in range(9):
                z.writestr('extension%d.xsd'%i, b'<xs:schema/>')
        with self.assertRaises(ProbeError):
            _instance_from_zip(raw.getvalue())
        raw2 = BytesIO()
        with ZipFile(raw2,'w') as z:
            z.writestr('instance.xbrl',xbrl())
            z.writestr('extension.xsd',b'<!DOCTYPE x [<!ENTITY a "x">]><x/>')
        with self.assertRaises(ProbeError):
            _instance_from_zip(raw2.getvalue())


    def test_revenue_fact_provenance_matches_exact_krw_year_and_context_without_approval(self):
        original = xbrl().decode('utf-8')
        original = original.replace(
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>',
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>'
            '<xbrli:segment><di:explicitMember '
            'dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            'test:SeparateMember</di:explicitMember></xbrli:segment>')
        original = original.replace('xmlns:test="http://example.com/test"',
            'xmlns:test="http://example.com/test" '
            'xmlns:di="http://xbrl.org/2006/xbrldi"')
        result = compare_annual_xbrl(
            FakeDART(zip_xml(original.encode('utf-8'))),
            CORP, 2025, ASOF, RECEIPT)
        records = result['metrics']['revenue']['xbrl_revenue_fact_context_evidence']
        self.assertEqual(len(records),1)
        fact = records[0]
        self.assertEqual(fact['concept_local'],'Revenue')
        self.assertTrue(fact['current_api_amount_equal'])
        self.assertTrue(fact['unit_verified_krw'])
        self.assertTrue(fact['fiscal_period_verified'])
        self.assertTrue(fact['dimension_axes_exactly_separate_pattern_local_only'])
        for key in ('concept_qname_sha256_prefix','context_ref_sha256_prefix',
                    'issuer_id_sha256_prefix','issuer_scheme_sha256_prefix'):
            self.assertEqual(len(fact[key]),16)
        self.assertFalse(fact['role_to_numeric_fact_instance_membership_verified'])
        self.assertFalse(fact['issuer_identity_verified_against_corp_code'])
        self.assertFalse(fact['historical_pit_numeric_value_certified'])
        self.assertFalse(fact['confirmed_scoring_allowed'])
        self.assertNotIn('contextRef',json.dumps(records))
        self.assertNotIn('amount_krw',json.dumps(records))
        self.assertFalse(result['historic_numeric_version_equals_current_api_verified'])
        self.assertFalse(result['confirmed_scoring_allowed'])

    def test_mixed_separate_axis_is_not_a_unique_separate_only_fact(self):
        original=xbrl().decode('utf-8')
        original=original.replace(
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>',
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>'
            '<xbrli:segment><di:explicitMember '
            'dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            'test:SeparateMember</di:explicitMember>'
            '<di:explicitMember dimension="test:BusinessLineAxis">'
            'test:CorporateMember</di:explicitMember></xbrli:segment>')
        original=original.replace('xmlns:test="http://example.com/test"',
            'xmlns:test="http://example.com/test" '
            'xmlns:di="http://xbrl.org/2006/xbrldi"')
        out=compare_annual_xbrl(FakeDART(zip_xml(original.encode())),
                                CORP,2025,ASOF,RECEIPT)
        m=out['metrics']['revenue']
        self.assertEqual(m['xbrl_requested_scope_pattern_only_candidate_count'],0)
        self.assertEqual(m['xbrl_revenue_fact_context_evidence'],[])
        self.assertFalse(m['scoring_allowed'])

    def test_overly_many_dimensions_fail_closed_not_silently_truncated(self):
        from official_probe.xbrl_compare import _context_axis_members
        from xml.etree import ElementTree as ET
        dims=''.join(
            '<di:explicitMember dimension="d:Axis%d">d:Member%d</di:explicitMember>' %(i,i)
            for i in range(17))
        root=ET.fromstring(
            '<xbrli:context xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:di="http://xbrl.org/2006/xbrldi" id="C">'
            '<xbrli:entity><xbrli:segment>'+dims+
            '</xbrli:segment></xbrli:entity></xbrli:context>')
        with self.assertRaises(ProbeError):
            _context_axis_members(root)


    def test_receipt_specific_original_numbers_are_private_candidates_not_pit(self):
        xml=xbrl().decode('utf-8').replace(
            'xmlns:test="http://example.com/test"',
            'xmlns:test="http://example.com/test" '
            'xmlns:di="http://xbrl.org/2006/xbrldi"')
        xml=xml.replace(
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>',
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>'
            '<xbrli:segment><di:explicitMember '
            'dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            'test:SeparateMember</di:explicitMember></xbrli:segment>')
        r=compare_annual_xbrl(FakeDART(zip_xml(xml.encode())),
                              CORP,2025,ASOF,RECEIPT)
        self.assertEqual(r['original_numeric_values_source'],'receipt_specific_xbrl_zip')
        self.assertFalse(r['historical_receipt_zip_content_immutability_verified'])
        self.assertFalse(r['original_numeric_values_eligible_for_pit_backtest'])
        for metric in ('revenue','operating_income','operating_cashflow',
                       'assets','liabilities','equity'):
            m=r['metrics'][metric]
            self.assertEqual(m['original_receipt_numeric_candidates_count'],1)
            cand=m['original_receipt_numeric_candidates_private'][0]
            self.assertEqual(cand['source'],'original_xbrl_zip_by_receipt')
            self.assertIs(type(cand['original_xbrl_amount_krw']),int)
            self.assertTrue(cand['current_api_amount_equal'])
            self.assertTrue(cand['unit_verified_krw'])
            self.assertTrue(cand['fiscal_period_verified'])
            self.assertFalse(cand['historical_pit_numeric_value_certified'])
            self.assertFalse(cand['complete_dts_and_fs_scope_verified'])
            self.assertFalse(cand['confirmed_scoring_allowed'])
        self.assertEqual(r['metrics']['revenue'][
            'original_receipt_numeric_candidates_private'][0]['original_xbrl_amount_krw'],1000)
        self.assertFalse(r['confirmed_scoring_allowed'])

    def test_revenue_has_two_original_numbers_but_one_current_api_match(self):
        xml=xbrl().decode('utf-8').replace(
            'xmlns:test="http://example.com/test"',
            'xmlns:test="http://example.com/test" '
            'xmlns:di="http://xbrl.org/2006/xbrldi"')
        xml=xml.replace(
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>',
            '<xbrli:identifier scheme="test">01199727</xbrli:identifier>'
            '<xbrli:segment><di:explicitMember '
            'dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            'test:SeparateMember</di:explicitMember></xbrli:segment>')
        xml=xml.replace(
            '<ifrs:Revenue contextRef="D" unitRef="U" decimals="0">1000</ifrs:Revenue>',
            '<ifrs:Revenue contextRef="D" unitRef="U" decimals="0">1000</ifrs:Revenue>'
            '<ifrs:RevenueFromContractsWithCustomers contextRef="D" unitRef="U" decimals="0">'
            '125</ifrs:RevenueFromContractsWithCustomers>')
        r=compare_annual_xbrl(FakeDART(zip_xml(xml.encode())),
                              CORP,2025,ASOF,RECEIPT)
        m=r['metrics']['revenue']
        c=m['original_receipt_numeric_candidates_private']
        self.assertEqual(len(c),2)
        self.assertEqual(sorted(v['original_xbrl_amount_krw'] for v in c),
                         [125,1000])
        self.assertEqual(sum(v['current_api_amount_equal'] for v in c),1)
        self.assertEqual(m['original_receipt_numeric_candidates_count'],2)
        self.assertFalse(m['original_receipt_numeric_candidates_scope_certified'])
        self.assertFalse(m['original_receipt_numeric_candidates_pit_certified'])
        self.assertFalse(r['historical_numeric_snapshot_verified'])

    def test_requested_ofs_scope_axis_only_is_diagnostic_not_certification(self):
        xml = xbrl(dimensioned=True).replace(
            b'xmlns:test="http://example.com/test"',
            b'xmlns:test="http://example.com/test" '
            b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"').replace(
            b'<test:dimension>OFS</test:dimension>',
            b'<xbrldi:explicitMember dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            b'test:SeparateMember</xbrldi:explicitMember>')
        out = compare_annual_xbrl(FakeDART(zip_xml(xml)), CORP, 2025, ASOF, RECEIPT)
        metric = out['metrics']['revenue']
        self.assertEqual(metric['xbrl_requested_scope_pattern_only_candidate_count'], 1)
        self.assertEqual(metric['xbrl_requested_scope_pattern_only_api_equal_count'], 1)
        self.assertFalse(metric['xbrl_requested_scope_pattern_is_verified'])
        self.assertEqual(metric['status'], 'xbrl_fact_or_scope_ambiguous')
        self.assertIsNone(metric['xbrl_amount_krw'])
        self.assertFalse(out['xbrl_accounting_scope_verified'])
        self.assertFalse(out['confirmed_scoring_allowed'])
        self.assertFalse(out['investment_data_ready'])

        # The requested CFS API basis must agree with the mock API rows.
        cfs_rows = api_rows()
        for row in cfs_rows:
            row['fs_div'] = 'CFS'
        out_cfs = compare_annual_xbrl(
            FakeDART(zip_xml(xml), rows=cfs_rows), CORP, 2025, ASOF,
            RECEIPT, fs_div='CFS')
        self.assertIsNone(
            out_cfs['metrics']['revenue']['xbrl_requested_scope_pattern_only_candidate_count'])
        self.assertFalse(out_cfs['xbrl_accounting_scope_verified'])

    def test_additional_note_axis_cannot_be_counted_as_primary_scope_candidate(self):
        xml = xbrl(dimensioned=True).replace(
            b'xmlns:test="http://example.com/test"',
            b'xmlns:test="http://example.com/test" '
            b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"').replace(
            b'<test:dimension>OFS</test:dimension>',
            b'<xbrldi:explicitMember dimension="test:ConsolidatedAndSeparateFinancialStatementsAxis">'
            b'test:SeparateMember</xbrldi:explicitMember>'
            b'<xbrldi:explicitMember dimension="test:MajorCustomersAxis">'
            b'test:CustomerAMember</xbrldi:explicitMember>')
        out = compare_annual_xbrl(FakeDART(zip_xml(xml)), CORP, 2025, ASOF, RECEIPT)
        metric = out['metrics']['revenue']
        self.assertEqual(metric['xbrl_candidate_api_amount_equal_count'], 1)
        self.assertEqual(metric['xbrl_requested_scope_pattern_only_candidate_count'], 0)
        self.assertEqual(metric['xbrl_requested_scope_pattern_only_api_equal_count'], 0)
        self.assertFalse(out['confirmed_scoring_allowed'])

    def test_candidate_amount_equality_diagnostic_never_overrides_ambiguity(self):
        c = FakeDART(zip_xml(xbrl(dimensioned=True, revenue=1001)))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        metric = out['metrics']['revenue']
        self.assertEqual(metric['xbrl_candidate_api_amount_equal_count'], 0)
        self.assertEqual(metric['xbrl_unparsed_dimensioned_fact_count'], 1)
        self.assertEqual(metric['status'], 'xbrl_fact_or_scope_ambiguous')
        self.assertFalse(out['asof_join_validated'])

    def test_wrong_period_or_currency_not_matched(self):
        for case in (xbrl(period_end='2024-12-31'), xbrl(unit='iso4217:USD')):
            with self.subTest(case=case[:100]):
                out = compare_annual_xbrl(FakeDART(zip_xml(case)), CORP,
                                          2025, ASOF, RECEIPT)
                self.assertEqual(out['metrics']['revenue']['status'],
                                 'matching_xbrl_fact_absent')

    def test_missing_api_value_not_guessed_from_xbrl(self):
        rows = [r for r in api_rows() if r['account_id'] != 'ifrs-full_Revenue']
        out = compare_annual_xbrl(FakeDART(rows=rows), CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['metrics']['revenue']['status'],
                         'api_account_missing_or_ambiguous')

    def test_revision_blocks_before_api_and_zip_requests(self):
        amended='20260715000001'
        c = FakeDART(filings=[filing(),
                              filing(amended, '[정정]사업보고서 (2025.12)')])
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['status'], 'revision_history_blocks_single_report_comparison')
        self.assertEqual(len(c.api_calls),1)
        self.assertFalse(c.xbrl_calls)

    def test_dart_documented_response_omits_fs_div_without_proving_scope(self):
        rows = api_rows()
        for row in rows:
            del row['fs_div']
        c = FakeDART(rows=rows)
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT, 'OFS')
        self.assertEqual(out['status'], 'six_values_observed_equal_scope_unverified')
        self.assertFalse(out['financial_api_fs_div_explicit_in_rows'])
        self.assertFalse(out['xbrl_accounting_scope_verified'])
        self.assertFalse(out['confirmed_scoring_allowed'])
        self.assertFalse(out['investment_data_ready'])
        self.assertEqual(c.xbrl_calls, [(RECEIPT, '11011')])

    def test_explicit_wrong_fs_div_blocks_xbrl_download(self):
        rows = api_rows()
        for row in rows:
            row['fs_div'] = 'CFS'
        c = FakeDART(rows=rows)
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT, 'OFS')
        self.assertEqual(out['status'], 'financial_api_report_identity_mismatch')
        self.assertTrue(out['financial_api_fs_div_explicit_in_rows'])
        self.assertFalse(c.xbrl_calls)
        self.assertFalse(out['confirmed_scoring_allowed'])

    def test_financial_api_receipt_mismatch_blocks_zip(self):
        changed='20260316001122'
        c = FakeDART(rows=api_rows(receipt=changed))
        out = compare_annual_xbrl(c, CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['status'], 'financial_api_report_identity_mismatch')
        self.assertFalse(c.xbrl_calls)

    def test_multiple_or_missing_xbrl_instances_not_certified(self):
        blank = zip_xml(b'<root/>')
        out = compare_annual_xbrl(FakeDART(blank), CORP, 2025, ASOF, RECEIPT)
        self.assertEqual(out['status'], 'xbrl_instance_missing_or_ambiguous')
        b = BytesIO()
        with ZipFile(b,'w') as z:
            z.writestr('one.xbrl', xbrl())
            z.writestr('two.xbrl', xbrl())
        out = compare_annual_xbrl(FakeDART(b.getvalue()),CORP,2025,ASOF,RECEIPT)
        self.assertEqual(out['status'], 'xbrl_instance_missing_or_ambiguous')

    def test_malicious_archive_rejected(self):
        for payload in (b'notzip', zip_xml(xbrl(), name='../report.xbrl'),
                        zip_xml(b'<!DOCTYPE x [ <!ENTITY x "evil"> ]><xbrli:xbrl/>')):
            with self.subTest(payload=payload[:12]), self.assertRaises(ProbeError):
                _instance_from_zip(payload)

    def test_original_xbrl_client_request_is_secret_safe(self):
        class Response:
            status_code=200
            content=zip_xml()
        class Session:
            def __init__(self):self.calls=[]
            def get(self,url,**kwargs):
                self.calls.append((url,kwargs))
                return Response()
        session=Session()
        blob = DARTClient('SECRET_KEY',session=session).original_xbrl_zip(RECEIPT,'11011')
        self.assertEqual(blob,Response.content)
        url, kw = session.calls[0]
        self.assertTrue(url.endswith('fnlttXbrl.xml'))
        self.assertEqual(kw['params']['rcept_no'],RECEIPT)
        self.assertEqual(kw['params']['reprt_code'],'11011')
        self.assertNotIn('SECRET_KEY',url)
        self.assertFalse(kw['allow_redirects'])
        with self.assertRaises(ValueError):
            DARTClient('SECRET_KEY',session=session).original_xbrl_zip('bad')

    def test_cli_explicit_one_company_live_only(self):
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'data.json'
            src.write_text(json.dumps({'date':ASOF,'stocks':[
                {'code':'252990','market':'KOSDAQ'},
                {'code':'491000','market':'KOSDAQ'}]}), encoding='utf-8')
            base=['--asof',ASOF,'--provider','dart','--xbrl-compare',
                  '--xbrl-receipt',RECEIPT,'--source',str(src)]
            for args in [
                base+['--symbols','252990'],
                base+['--live','--symbols','252990,491000'],
                base+['--live','--symbols','252990','--history-sample'],
                base+['--live','--symbols','252990','--revision-audit',
                      '--revision-expected-receipt',RECEIPT],
            ]:
                with self.subTest(args=args), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):run(args)


    def test_batch_parser_bounds_and_strict_receipts(self):
        from official_probe.__main__ import _batch_cases
        cases = ('252990:20260316001120:OFS,'
                 '280360:20260330001120:CFS')
        self.assertEqual(list(_batch_cases(cases, ASOF)), ['252990', '280360'])
        for invalid in (
            '252990:20260316001120:OFS',
            '252990:20260316001120:OFS,252990:20260316001120:OFS',
            '252990:20260316001120:OFS,280360:20260330001120:BAD',
            '252990:20260316001120:OFS,280360:20270201001120:CFS',
            '252990:20260316001120:OFS,280360:20260230001120:CFS',
            '252990:20260316001120:OFS,280360:20261016001120:CFS',
            '252990:20260316001120:OFS,280360:20260330001120:CFS,'
            '018880:20260331001120:OFS,014680:20260401001120:OFS,'
            '064290:20260402001120:OFS,300720:20260403001120:OFS',
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _batch_cases(invalid, ASOF)

    def test_batch_cli_rejects_before_network_on_invalid_mode_or_symbol_order(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'data.json'
            src.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code': '252990', 'market': 'KOSDAQ'},
                {'code': '280360', 'market': 'KOSPI'}]}), encoding='utf-8')
            cases = '252990:20260316001120:OFS,280360:20260330001120:CFS'
            base = ['--asof', ASOF, '--provider', 'dart', '--symbols',
                    '252990,280360', '--xbrl-batch-cases', cases,
                    '--source', str(src)]
            for args in (
                base,
                base + ['--xbrl-compare', '--xbrl-receipt', RECEIPT],
                base + ['--live', '--finance-sample'],
                base + ['--live', '--revision-audit',
                        '--revision-expected-receipt', RECEIPT],
                ['--asof', ASOF, '--provider', 'dart', '--symbols',
                 '280360,252990', '--xbrl-batch-cases', cases,
                 '--source', str(src), '--live'],
            ):
                with self.subTest(args=args), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        with patch('official_probe.__main__.DARTClient', side_effect=AssertionError('network attempted')):
                            run(args)

    def test_batch_live_is_bounded_and_keeps_all_scope_flags_false(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'data.json'
            output = Path(root) / 'result.json'
            src.write_text(json.dumps({'date': ASOF, 'stocks': [
                {'code': '252990', 'market': 'KOSDAQ'},
                {'code': '280360', 'market': 'KOSPI'}]}), encoding='utf-8')
            calls = []
            class BatchClient:
                def __init__(self, key, session=None):
                    self.key = key
                def corp_mapping(self):
                    return {'252990': CORP, '280360': '00123456'}
                def recent_filings(self, corp, start, end):
                    return []
            def fake_compare(client, corp, business_year, asof, receipt, fs_div):
                calls.append((corp, business_year, asof, receipt, fs_div))
                return {'status': 'partial_or_different_values_unverified',
                        'confirmed_scoring_allowed': False,
                        'investment_data_ready': False}
            with patch.dict(os.environ, {'DART_API_KEY': 'OFFLINE_PLACEHOLDER'}):
                with patch('official_probe.__main__.DARTClient', BatchClient):
                    with patch('official_probe.__main__.compare_annual_xbrl', side_effect=fake_compare):
                        result = run(['--asof', ASOF, '--provider', 'dart', '--live',
                            '--symbols', '252990,280360',
                            '--xbrl-batch-cases',
                            '252990:20260316001120:OFS,280360:20260330001120:CFS',
                            '--source', str(src), '--output', str(output)])
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][-2:], (RECEIPT, 'OFS'))
            self.assertEqual(calls[1][-2:], ('20260330001120', 'CFS'))
            self.assertEqual(result['dart']['xbrl_batch_pilot']['requested_cases'], 2)
            self.assertEqual(result['dart']['xbrl_batch_pilot']['completed_case_reports'], 2)
            self.assertFalse(result['investment_data_ready'])
            self.assertFalse(result['dart']['xbrl_batch_pilot']['accounting_scope_verified'])
            self.assertFalse(result['confirmed_scoring_allowed'])
            self.assertEqual(len(result['dart']['companies_sampled']), 2)
            self.assertTrue(output.exists())

    def test_cli_missing_key_does_not_network(self):
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'data.json'
            out=Path(root)/'out.json'
            src.write_text(json.dumps({'date':ASOF,'stocks':[
                {'code':'252990','market':'KOSDAQ'}]}), encoding='utf-8')
            with patch.dict(os.environ,{'DART_API_KEY':''}):
                data=run(['--asof',ASOF,'--provider','dart','--live',
                          '--xbrl-compare','--xbrl-receipt',RECEIPT,
                          '--xbrl-fs-div','OFS','--symbols','252990',
                          '--source',str(src),'--output',str(out)])
            self.assertEqual(data['dart']['status'],'key_required')
            self.assertFalse(data['investment_data_ready'])


if __name__ == '__main__':
    unittest.main()
