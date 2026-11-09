import copy
import json
from pathlib import Path
import unittest

import v3_dart_xbrl_capex_pilot as p
import test_v3_dart_xbrl_capex_pilot as fixtures

DAY='2026-11-05'


def missing_years(count=1):
    records={};sources={}
    for i in range(count):
        code=f'{910000+i:06d}'
        years={};major={}
        for y in (2023,2024,2025):
            receipt=f'{y+1}0318000001'
            years[str(y)]={'major_annual_receipt_matched':True,'annual_receipt_id':receipt,
                'operating_cashflow_krw':100,'ppe_capex_krw':None,'intangible_capex_krw':None}
            major[str(y)]={'CFS':{'receipt_id':receipt}}
        records[code]={'scope':'CFS','years':years};sources[code]={'years':major}
    return {'records':records},{'records':sources}


class GapSamplingTests(unittest.TestCase):
    def test_older_missing_years_are_not_hidden_by_a_newer_missing_year(self):
        state,major=missing_years()
        chosen=[p.select_candidates(state,major,asof=DAY,cohort_index=i) for i in range(3)]
        self.assertEqual({row[0][1] for row in chosen},{2023,2024,2025})
        self.assertTrue(all(len(rows)==1 for rows in chosen))
        self.assertEqual(chosen[0],p.select_candidates(state,major,asof=DAY,cohort_index=0))

    def test_unused_slots_fill_with_distinct_issuers_without_starving_years(self):
        state,major=missing_years(4)
        seen=set()
        for cohort in range(12):
            rows=p.select_candidates(state,major,asof=DAY,cohort_index=cohort)
            self.assertEqual(len(rows),4)
            self.assertEqual(len({r[0] for r in rows}),4)
            seen.update((r[0],r[1]) for r in rows)
        self.assertEqual(len(seen),12)

    def test_priority_keeps_general_exploration_and_existing_call_cap(self):
        state,major=missing_years(8);priority={'910006','910007'}
        seen=set()
        for cohort in range(24):
            rows=p.select_candidates(state,major,asof=DAY,cohort_index=cohort,priority_codes=priority)
            self.assertEqual(len(rows),4)
            self.assertEqual(len({r[0] for r in rows}),4)
            self.assertEqual(sum(r[0] in priority for r in rows),2)
            seen.update(r[0] for r in rows)
        self.assertEqual(seen,set(state['records']))

    def test_old_receipt_mismatch_is_checked_before_rotation_and_provider(self):
        state,major=missing_years()
        major['records']['910000']['years']['2023']['CFS']['receipt_id']='20240101000002'
        with self.assertRaises(p.XbrlPilotError):p.select_candidates(state,major,asof=DAY)
        for invalid in ('910000',{'bad'},[True]):
            with self.assertRaises(p.XbrlPilotError):
                p.select_candidates(*missing_years(),asof=DAY,priority_codes=invalid)

    def test_priority_is_rebuilt_from_reconciled_parts_not_untrusted_rank_hint(self):
        root=Path(__file__).resolve().parents[1]/'docs'
        def read(name):return json.loads((root/name).read_text())
        report=read('v3-operational-research-scores.json')
        comparison=read('v3-operational-research-comparison.json')
        catalog=read('v22-market-catalog.json');major=read('v3-dart-bulk-history.json')
        if any(x.get('asof') != catalog['date'] for x in (report,major)):
            self.skipTest('priority comparison requires reconciled market and annual histories')
        got=p.operational_gap_priority(report,comparison,catalog,major)
        queue=read('v3-operational-evidence-priority.json')['single_component_queue']
        expected={r['code'] for r in queue if r['gaps'][0]['component'] in ('cashflow_fcf_persistence','fcf_yield')}
        self.assertTrue(expected<=got)
        full=read('v3-operational-evidence-priority.json')['source_family_completion_upper_bounds']['full_100_if_all_missing_family_facts_became_observed']['dart_cashflow_capex']
        self.assertEqual(len(got),full)
        bad=copy.deepcopy(comparison);bad['operational_coverage']['available_100_count']+=1
        with self.assertRaises(p.XbrlPilotError):p.operational_gap_priority(report,bad,catalog,major)
        old={**major,'asof':'2026-11-04'}
        self.assertEqual(p.operational_gap_priority(report,comparison,catalog,old),set())

    def test_live_probe_remains_anonymous_and_never_adopts_a_fact_amount(self):
        market,catalog,major,state,code,receipt=fixtures.OriginalXbrlEvidenceTests().fixture()
        caller=fixtures.Session(fixtures.archive(fixtures.sample_xml()))
        report=p.run_pilot(market,catalog,major,state,mode='live',key='A'*40,
            session=caller,priority_codes={code})
        self.assertEqual(report['provider_xbrl_file_calls'],1)
        self.assertEqual(len(caller.requests),1)
        self.assertEqual(report['operational_gap_priority_sampled_issuers'],1)
        self.assertTrue(report['operational_gap_priority_is_not_investment_rank'])
        self.assertTrue(report['no_xbrl_amounts_or_investment_scores_derived'])
        self.assertTrue(report['no_statement_scope_verified'])
        for secret in (code,receipt,'A'*40):self.assertNotIn(secret,json.dumps(report))
