import copy
import unittest
from v3_money_saved_structure_replay import verify
import test_v3_actual_money_structure as structure_tests
from v3_actual_money_structure import derive


class SavedMoneyReplayTests(unittest.TestCase):
    def sources(self):
        d,w,c=structure_tests.MoneyStructureTests().inputs()
        for row in c[:12]:row['intrabucket_price_change_bp']=100
        new={'asof':'2026-11-05','revision':'a'*40,'records':{'000001':{
            'issuer_name':'Synthetic','asof':'2026-10-30','points':2.5,
            'r1c_structure':derive(d,w,c)}}}
        old=copy.deepcopy(new)
        part=old['records']['000001']['r1c_structure']
        part.pop('correction_window_cohorts')
        part['components']['correction_contraction']=None
        return old,new

    def test_only_newly_observed_correction_can_change_without_relabeling(self):
        old,new=self.sources();before=copy.deepcopy((old,new))
        self.assertEqual(verify(old,new),1)
        self.assertEqual((old,new),before)
        self.assertEqual(verify(new,new),0)

    def test_source_identity_date_and_unrelated_score_changes_fail_closed(self):
        old,new=self.sources()
        for change in (
            lambda x:x.update(asof='2026-11-06'),
            lambda x:x.update(revision='b'*40),
            lambda x:x['records']['000001'].update(points=3),
            lambda x:x['records']['000001'].update(issuer_name='Other'),
            lambda x:x['records']['000001'].update(asof='2026-11-06'),
            lambda x:x['records']['000001']['r1c_structure']['components'].update(persistence=0),
            lambda x:x['records'].clear(),
        ):
            changed=copy.deepcopy(new);change(changed)
            with self.assertRaises(ValueError):verify(old,changed)

    def test_existing_correction_cannot_be_revised_or_lost(self):
        _,old=self.sources()
        for value in (None,0):
            changed=copy.deepcopy(old)
            changed['records']['000001']['r1c_structure']['components']['correction_contraction']=value
            with self.assertRaises(ValueError):verify(old,changed)
