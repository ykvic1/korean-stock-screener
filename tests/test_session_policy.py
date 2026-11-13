import unittest
from session_policy import assess,input_fingerprint,REQUIRED_SESSION,CONSOLIDATED_VOLUME,required_volume_session
class SessionTests(unittest.TestCase):
    def good(self):return dict(price_session=REQUIRED_SESSION,volume_session=CONSOLIDATED_VOLUME,final=True,official_close_status='matched',official_source_sha256='abc',history_session_verified=True,asof='2026-10-14',received_at='2026-10-14T21:30:00+09:00')
    def test_era_boundary_requires_different_volume_scope(self):
        for day,scope in [('2025-03-03',REQUIRED_SESSION),('2025-03-04',CONSOLIDATED_VOLUME),('2026-11-13',CONSOLIDATED_VOLUME)]:
            with self.subTest(day=day):
                e=dict(self.good(),asof=day,volume_session=scope,received_at=day+'T21:00:00+09:00')
                self.assertTrue(assess(e)['confirmed_scoring_allowed'])
                self.assertEqual(assess(e)['required_volume_session'],scope)
                e['volume_session']=CONSOLIDATED_VOLUME if scope==REQUIRED_SESSION else REQUIRED_SESSION
                self.assertFalse(assess(e)['confirmed_scoring_allowed'])
    def test_invalid_date_never_selects_a_volume_era(self):
        for day in (None,'2026-2-1','2026-02-30','20261113'):
            with self.subTest(day=day):
                self.assertFalse(assess(dict(self.good(),asof=day))['confirmed_scoring_allowed'])
                with self.assertRaises(ValueError):required_volume_session(day)
    def test_consolidated_label_does_not_replace_history_or_source_proof(self):
        for field,value in [('official_source_sha256',None),('history_session_verified',False),('official_close_status','unverified'),('final',False)]:
            with self.subTest(field=field):
                self.assertFalse(assess(dict(self.good(),**{field:value}))['confirmed_scoring_allowed'])
    def test_final_flag_cannot_bypass_aftermarket_cutoff(self):
        for stamp in ('2026-10-14T20:59:59+09:00','2026-10-14T21:00:00',None,'invalid','2026-10-13T21:00:00+09:00'):
            with self.subTest(stamp=stamp):
                self.assertFalse(assess(dict(self.good(),received_at=stamp))['confirmed_scoring_allowed'])
        self.assertTrue(assess(dict(self.good(),received_at='2026-10-14T12:00:00+00:00'))['confirmed_scoring_allowed'])
    def test_unknown_never_scores(self):self.assertFalse(assess({})['confirmed_scoring_allowed'])
    def test_krx_total_volume_is_not_regular(self):
        e=self.good();e['volume_session']='KRX_ALL_SESSIONS';self.assertFalse(assess(e)['confirmed_scoring_allowed'])
    def test_receipt_time_does_not_prove_session(self):
        e=self.good();e['price_session']='NXT';self.assertFalse(assess(e)['confirmed_scoring_allowed'])
    def test_predictive_validation_not_required(self):self.assertTrue(assess(self.good())['confirmed_scoring_allowed'])
    def test_price_correction_invalidates(self):self.assertNotEqual(input_fingerprint([100],self.good()),input_fingerprint([101],self.good()))
    def test_partial_history_blocks(self):
        e=self.good();e['history_session_verified']=False;self.assertFalse(assess(e)['confirmed_scoring_allowed'])
