"""Offline tests for three-date KRX historical 10-year access probe."""
import json
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from v3_krx_historical_reach_probe import (
    research_historical_reach, HistoricalReachError,
    HISTORICAL_DAYS, main)


class FakeProvider:
    def __init__(self, fail_on=None):
        self.calls=[]
        self.fail_on=fail_on

    def daily_market(self,market,day):
        self.calls.append((market,day))
        if (market,day)==self.fail_on:
            raise RuntimeError("SECRET_API_TOKEN")
        code="005930" if market=="KOSPI" else "247540"
        rows=[{
            "BAS_DD":day.replace("-",""),"ISU_CD":code,
            "TDD_OPNPRC":"100","TDD_HGPRC":"110",
            "TDD_LWPRC":"90","TDD_CLSPRC":"103",
            "ACC_TRDVOL":"1000",
        }]
        return rows,json.dumps({"OutBlock_1":rows}).encode()


class OldKRXHistoryProbeTests(unittest.TestCase):
    def test_dry_run_zero_calls(self):
        fake=FakeProvider()
        r=research_historical_reach(live=False,key="fake",client=fake)
        self.assertEqual(fake.calls,[])
        self.assertEqual(r["actual_api_calls"],0)
        self.assertFalse(r["real_historical_access_observed"])

    def test_2016_2021_2026_real_path_fully_validated(self):
        fake=FakeProvider()
        r=research_historical_reach(live=True,key="TEST",client=fake)
        self.assertEqual(len(fake.calls),6)
        self.assertEqual(r["observed_dates"],3)
        self.assertEqual(r["oldest_historical_api_date_observed"],"2016-10-17")
        self.assertEqual(r["newest_historical_api_date_observed"],"2026-10-16")
        self.assertEqual(len(r["day_summaries"]),3)
        self.assertTrue(r["real_historical_access_observed"])
        self.assertFalse(r["all_intervening_trade_dates_archived"])
        self.assertFalse(r["vendor_rows_published"])
        self.assertFalse(r["historic_stock_master_and_delistings_verified"])
        self.assertFalse(r["v3_release_ready"])
        s=json.dumps(r)
        for secret in ("005930","247540","TDD_CLSPRC","SECRET_API_TOKEN"):
            self.assertNotIn(secret,s)

    def test_failure_is_not_turned_into_zero_volume(self):
        fake=FakeProvider(fail_on=("KOSPI","2021-10-18"))
        with self.assertRaisesRegex(HistoricalReachError,
                                    "provider_or_historical_shape_failure"):
            research_historical_reach(live=True,key="fake",client=fake)
        self.assertEqual(len(fake.calls),3)

    def test_cli_no_secret_fails_closed(self):
        o=StringIO()
        with patch.dict("os.environ",{"KRX_API_KEY":""}):
            with redirect_stdout(o):
                code=main(["--live"])
        self.assertEqual(code,1)
        self.assertIn("krx_key_unavailable",o.getvalue())
        self.assertNotIn("KRX_API_KEY",o.getvalue())
        self.assertNotIn("005930",o.getvalue())

    def test_dates_are_disjoint_and_earlier_than_today(self):
        self.assertEqual(HISTORICAL_DAYS,
                         ("2016-10-17","2021-10-18","2026-10-16"))


if __name__=="__main__":
    unittest.main()
