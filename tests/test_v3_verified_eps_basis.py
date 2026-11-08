import copy
import unittest
from unittest.mock import patch
import v3_verified_eps_basis as v
import v3_operational_research_score as ops
from test_v3_eps_basis_audit import Provider,assess,record,context


class VerifiedEPSBasisTests(unittest.TestCase):
    def fixture(self,direct=False):
        ctx=context();ctx['baseline']['observed_on']='2026-11-06';ctx['research']['asof']='2026-11-06'
        proof=record(assess(Provider(direct=direct)),ctx)
        inputs=[{'date':'2026-11-06'},{},{'source_sha256':{'annual_book':v._stable_digest({})}},{},{},{},
            {'schema':v.audit.SCHEMA,'asof':'2026-11-06','scores_unchanged':True,'records':{'000001':proof}}]
        return ctx,inputs

    def test_verified_chain_adds_actual_three_year_values_without_mutating_source(self):
        ctx,inputs=self.fixture();before=copy.deepcopy([ctx,inputs])
        with patch.object(v.audit,'contexts',return_value={'000001':ctx}):out,summary=v.admit(*inputs)
        self.assertEqual(out['000001']['reported_basic_eps_2023_2025'],[-10.,0.,30.])
        self.assertEqual(summary['accepted'],1);self.assertEqual(summary['provider_calls'],0)
        self.assertEqual([ctx,inputs],before)
        self.assertEqual(out['000001']['comparative_basis'],v.audit.CONTRACT)

    def test_complete_single_filing_has_independent_admission_path(self):
        ctx,inputs=self.fixture(direct=True)
        with patch.object(v.audit,'contexts',return_value={'000001':ctx}):out,summary=v.admit(*inputs)
        self.assertEqual(len(out),1);self.assertEqual(summary['reasons'],{'same_filing_eps_completed':1})

    def test_wrong_issuer_receipt_input_digest_or_tampered_proof_cannot_score(self):
        for mutate in (lambda r:r.update(code='000002'),lambda r:r.update(input_digest='b'*64),
            lambda r:r['receipts'].update({'2024':'20250319000001'}),lambda r:r['evidence']['eps'].__setitem__(0,99.)):
            ctx,inputs=self.fixture();mutate(inputs[-1]['records']['000001'])
            with patch.object(v.audit,'contexts',return_value={'000001':ctx}):out,_=v.admit(*inputs)
            self.assertEqual(out,{})
        ctx,inputs=self.fixture();ctx['receipts']['2024']='20250319000001'
        with patch.object(v.audit,'contexts',return_value={'000001':ctx}):out,_=v.admit(*inputs)
        self.assertEqual(out,{})

    def test_blocked_stale_or_unmatched_book_stays_unavailable(self):
        for case in ('blocked','stale','book','missing','malformed_hash'):
            ctx,inputs=self.fixture()
            if case=='blocked':inputs[-1]['records']['000001']=v.audit.make_record(ctx,day='2026-11-06',checked_on='2026-11-09',reason='weighted_ordinary_shares_missing')
            if case=='stale':inputs[-1]['asof']='2026-11-05'
            if case=='book':inputs[1]['changed']=True
            if case=='missing':inputs[-1]=None
            if case=='malformed_hash':inputs[2]['source_sha256']=None
            with patch.object(v.audit,'contexts',return_value={'000001':ctx}):out,_=v.admit(*inputs)
            self.assertEqual(out,{})

    def test_verified_eps_changes_only_existing_growth_stability_available_weight(self):
        ctx,inputs=self.fixture()
        with patch.object(v.audit,'contexts',return_value={'000001':ctx}):out,_=v.admit(*inputs)
        stub={'code':'000001','name':'issuer','market':'KOSPI','factors':{}}
        plain,_=ops.score_issuer(None,stub,day='2026-11-06')
        upgraded,_=ops.score_issuer(None,stub,day='2026-11-06',reported_eps=out['000001'])
        self.assertIsNone(plain['valuation']['per_eps_quality_growth'])
        self.assertAlmostEqual(upgraded['valuation']['per_eps_quality_growth']['available'],10/3,places=6)
        for area,parts in plain.items():
            for key,value in parts.items():
                if (area,key)!=('valuation','per_eps_quality_growth'):self.assertEqual(upgraded[area][key],value)
        self.assertEqual(sum(ops.BUDGET.values()),100)

    def test_workflow_replays_after_trusted_audit_completion(self):
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-operational-research-score.yml').read_text()
        self.assertIn('- V3 Latest Annual EPS Share-Basis Audit',text)
        self.assertIn('head_repository.full_name == github.repository',text)
        self.assertIn('v3_verified_eps_basis.py',text)
