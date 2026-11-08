import copy
from pathlib import Path
import unittest
import v3_dart_eps_prior_receipt_probe as p
import v3_dart_cashflow_enrichment as cf
from test_v3_dart_eps_prior_receipt_probe import CODE,ROW,ANCHOR
from test_v3_dart_xbrl_capex_pilot import archive
from test_v3_eps_explicit_context_recovery import ordinary_xml


def record(values,previous=None):
    return p.make_record(CODE,ROW,ANCHOR,values=values,reason='candidate_2023_verified',
        archive_sha256='a'*64,profile_sha256='b'*64,asof='2026-11-06',previous=previous)


def legacy(row):
    out=copy.deepcopy(row);out.pop('source_reader_version',None);out.pop('previous_review_digest',None)
    out['digest']=cf.fingerprint({k:v for k,v in out.items() if k!='digest'})
    return out


class PriorEPSContextContinuationTests(unittest.TestCase):
    def test_prior_ordinary_eps_has_independent_scope_only_cfo_and_exact_overlap(self):
        raw=ordinary_xml().replace(b'>0</i:BasicEarningsLossPerShare>',b'>3540</i:BasicEarningsLossPerShare>')
        raw=raw.replace(b'contextRef="C2025" unitRef="U"',b'contextRef="C2024" unitRef="U"')
        values,reason=p.extract_archive(archive(raw),scope='CFS',identifiers={'00123456'},cfo_anchor=100)
        self.assertEqual(values,{'2023':-20.,'2024':3540.})
        saved=record(values);self.assertEqual(saved['reason'],'candidate_2023_verified')
        self.assertTrue(saved['scored_eps_unchanged']);self.assertIsNone(ROW['reported_basic_eps_2023_2025'][0])
        bad=raw.replace(b'contextRef="C2024" unitRef="U"',b'contextRef="E2024" unitRef="U"')
        self.assertEqual(p.extract_archive(archive(bad),scope='CFS',identifiers={'00123456'},cfo_anchor=100)[0],{})

    def test_prior_preference_and_extra_classes_are_not_basic_ordinary_eps(self):
        for member in ('PreferenceSharesMember','ClassAMember'):
            raw=ordinary_xml(member=member).replace(b'contextRef="C2025" unitRef="U"',b'contextRef="C2024" unitRef="U"')
            self.assertEqual(p.extract_archive(archive(raw),scope='CFS',identifiers={'00123456'},cfo_anchor=100)[0],{})

    def test_old_positive_kept_old_negative_rechecked_once_with_previous_digest(self):
        source={'records':{CODE:ROW}};cash={'records':{CODE:{'years':{'2024':ANCHOR}}}}
        positive=legacy(record({'2023':100.,'2024':3540.}))
        self.assertEqual(p.candidates(source,cash,{CODE:positive}),[])
        old=legacy(record({'2024':3540.}));p.verify_record(old)
        self.assertEqual(len(p.candidates(source,cash,{CODE:old})),1)
        reviewed=record({'2024':3540.},previous=old)
        self.assertEqual(reviewed['previous_review_digest'],old['digest'])
        self.assertEqual(p.candidates(source,cash,{CODE:reviewed}),[])

    def test_reader_or_previous_digest_tampering_fails_without_reinterpreting_legacy(self):
        saved=record({'2024':3540.},previous=legacy(record({'2024':3540.})))
        for key,value in (('source_reader_version','unknown'),('previous_review_digest','bad')):
            bad=copy.deepcopy(saved);bad[key]=value
            bad['digest']=cf.fingerprint({k:v for k,v in bad.items() if k!='digest'})
            with self.assertRaises(p.PriorEPSError):p.verify_record(bad)
        p.verify_record(legacy(record({'2023':100.,'2024':3540.})))

    def test_provider_errors_are_not_missing_original_reviews(self):
        for status in ('020','010','800'):
            with self.assertRaises(p.xp.XbrlPilotError):
                p.require_archive_or_explicit_absence(f'<result><status>{status}</status><message>Unavailable</message></result>'.encode())
        p.require_archive_or_explicit_absence(b'<result><status>013</status><message>No data</message></result>')

    def test_workflow_uses_explicit_remaining_count_publishes_before_failure_and_never_scores(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-eps-prior-receipt-probe.yml').read_text()
        self.assertIn('remaining_source_reviews',text);self.assertNotIn("pending=data['eligible_total']",text)
        self.assertIn("steps.collect.outputs.reviewed != '0'",text)
        self.assertIn('head_repository.full_name == github.repository',text)
        self.assertIn('queue: max',text)
        self.assertLess(text.index('Publish read-only provenance'),text.index('Surface source failure'))
        self.assertIn('v3-eps-prior-receipt-probe.yml/dispatches',text)
        self.assertNotIn('v3-current-observed-scorecard.yml/dispatches',text)
        self.assertIn('git add docs/v3-eps-prior-original-receipt-research.json',text)


if __name__=='__main__':unittest.main()
