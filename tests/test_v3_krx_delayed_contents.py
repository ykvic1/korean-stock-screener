"""Test GitHub Contents readback delay without repeated source calls."""
import unittest
from unittest.mock import patch
from test_v3_krx_derived_5day import FakePrivate, FakeKRX
from v3_krx_derived_5day import store_five_day_factors

class SlowGit(FakePrivate):
    def put_new(self,path,blob,message):
        super().put_new(path,blob,message)
        self.hidden=2
    def get_content(self,path):
        if getattr(self,'hidden',0)>0:
            self.hidden-=1
            return None,None
        return super().get_content(path)

class DelayedReadTest(unittest.TestCase):
    def test_one_upload_source_ten_calls_with_two_delayed_gets(self):
        p=SlowGit()
        api=FakeKRX()
        with patch("time.sleep",return_value=None):
            out=store_five_day_factors(p,api)
        self.assertEqual(out['status'],'persisted_and_readback_verified_derived')
        self.assertEqual(len(api.calls),10)
        self.assertEqual(p.posts,1)
        rerun=FakeKRX()
        self.assertEqual(store_five_day_factors(p,rerun)['source_api_calls'],0)
        self.assertEqual(rerun.calls,[])

if __name__=='__main__':
    unittest.main()
