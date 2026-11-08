"""Completion status schedules original acquisition; it must not exclude data."""
from copy import deepcopy
from pathlib import Path
import unittest

import v3_dart_xbrl_owner_profit as recovery
from test_v3_dart_xbrl_owner_profit import missing, merge


def record(code):
    row=deepcopy(missing())
    row['code']=code
    return row


def anchor(row,**changes):
    return {'major_annual_receipt_matched':True,'scope':row['fs_div'],
            'annual_receipt_id':row['annual_receipt_id'],'operating_cashflow_krw':100,**changes}


def cash(rows):
    return {'records':{code:{'years':{'2025':value}} for code,value in rows.items()}}


class OriginalProfitQueueTests(unittest.TestCase):
    def test_multiple_gap_issuers_are_acquired_and_completion_targets_still_go_first(self):
        rows={code:record(code) for code in ('000001','000002','000003')}
        rows['000002'].update(status='quarantined_source_account',
            source_rejection_reason='annual_profit_amount_not_integer_krw')
        source=cash({code:anchor(row) for code,row in rows.items()})
        selected=recovery.recovery_candidates(rows,source,{},preferred={'000003'})
        self.assertEqual([code for code,_,_ in selected],['000003','000001','000002'])
        self.assertTrue(all(cfo==100 for _,_,cfo in selected))
        self.assertEqual(len(recovery.recovery_candidates(rows,source,{},preferred=set())),3)

    def test_existing_zero_loss_positive_and_unavailable_reviews_are_never_refetched(self):
        rows={code:record(code) for code in ('000001','000002','000003','000004','000005')}
        for code,value in [('000001',0),('000002',-100),('000003',100)]:
            rows[code]['owners_profit_krw']=value
        rows['000004']=recovery.merge(rows['000004'],None,[],reason='no_verified_owner_profit',
            cfo_anchor=100,archive_hash='a'*64,profile_hash='b'*64)
        source=cash({code:anchor(row) for code,row in rows.items()})
        selected=recovery.recovery_candidates(rows,source,{},preferred=set(rows))
        self.assertEqual([code for code,_,_ in selected],['000005'])

    def test_exact_scope_receipt_and_explicit_numeric_cfo_are_required(self):
        row=record('000001')
        for changes in ({'scope':'OFS'}, {'annual_receipt_id':'20260321000001'},
                        {'major_annual_receipt_matched':False}, {'operating_cashflow_krw':None},
                        {'operating_cashflow_krw':True}):
            self.assertEqual(recovery.recovery_candidates({'000001':row},
                cash({'000001':anchor(row,**changes)}),{}),[])
        selected=recovery.recovery_candidates({'000001':row},
            cash({'000001':anchor(row,operating_cashflow_krw=0)}),{})
        self.assertEqual(selected[0][2],0)

    def test_bulk_receipt_can_only_request_an_independent_same_scope_cfo(self):
        row=record('000001'); rows={'000001':row}
        source=cash({'000001':anchor(row,scope='OFS')})
        good={'records':{'000001':{'years':{'2025':{'CFS':{'receipt_id':row['annual_receipt_id']}}}}}}
        selected=recovery.recovery_candidates(rows,source,good)
        self.assertEqual(len(selected),1);self.assertIsNone(selected[0][2])
        for scope,receipt in [('OFS',row['annual_receipt_id']),('CFS','20260321000001')]:
            bad={'records':{'000001':{'years':{'2025':{scope:{'receipt_id':receipt}}}}}}
            self.assertEqual(recovery.recovery_candidates(rows,source,bad),[])

    def test_identity_mismatch_is_not_an_acquisition_target(self):
        row=record('000002')
        self.assertEqual(recovery.recovery_candidates({'000001':row},cash({'000001':anchor(row)}),{}),[])

    def test_unrelated_financial_gaps_do_not_change_original_proof_admission(self):
        row=record('000001')
        chosen=recovery.recovery_candidates({'000001':row},cash({'000001':anchor(row)}),{})
        self.assertEqual(len(chosen),1)
        accepted=merge(chosen[0][1])
        self.assertTrue(recovery.verify_review(accepted))
        bad=deepcopy(accepted);bad['original_xbrl_proof']['scope']='OFS'
        with self.assertRaises(recovery.OriginalProfitError):recovery.verify_review(bad)

    def test_workflow_continues_only_with_progress_and_preserves_pending_writers(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'.github/workflows/v3-xbrl-owner-profit.yml').read_text()
        self.assertIn("if: steps.collect.outputs.interrupted == '0' && steps.collect.outputs.completed_total != '0'",text)
        self.assertIn("if: steps.remaining.outputs.continue == 'true'",text)
        self.assertIn('eligible_missing_profit_issuers',text)
        self.assertIn('completed_total=$((completed_total + completed))',text)
        self.assertLess(text.index('Verify and publish derived observations'),
            text.index('Check remaining anchored originals'))
        self.assertIn('v3-xbrl-owner-profit.yml/dispatches',text)
        self.assertIn('-f ref=main',text)
        self.assertIn("cron: '03 11 * * 1-5'",text)
        for name in ('v3-xbrl-owner-profit.yml','v3-dart-owner-profit-probe.yml'):
            self.assertIn('queue: max',(root/'.github/workflows'/name).read_text())


if __name__=='__main__':unittest.main()
