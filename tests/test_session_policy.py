import unittest
from session_policy import assess,input_fingerprint,REQUIRED_SESSION
class SessionTests(unittest.TestCase):
    def good(self):return dict(price_session=REQUIRED_SESSION,volume_session=REQUIRED_SESSION,final=True,official_close_status='matched',official_source_sha256='abc',history_session_verified=True,asof='2026-10-14',received_at='2026-10-14T16:00:00+09:00')
    def test_unknown_never_scores(self):self.assertFalse(assess({})['confirmed_scoring_allowed'])
    def test_krx_total_volume_is_not_regular(self):
        e=self.good();e['volume_session']='KRX_ALL_SESSIONS';self.assertFalse(assess(e)['confirmed_scoring_allowed'])
    def test_receipt_time_does_not_prove_session(self):
        e=self.good();e['price_session']='NXT';self.assertFalse(assess(e)['confirmed_scoring_allowed'])
    def test_predictive_validation_not_required(self):self.assertTrue(assess(self.good())['confirmed_scoring_allowed'])
    def test_price_correction_invalidates(self):self.assertNotEqual(input_fingerprint([100],self.good()),input_fingerprint([101],self.good()))
    def test_partial_history_blocks(self):
        e=self.good();e['history_session_verified']=False;self.assertFalse(assess(e)['confirmed_scoring_allowed'])
