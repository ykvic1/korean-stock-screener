"""Research-only KRX live calendar challenge: wholly mocked official API.

The XKRX calendar is third-party and cannot assert exchange-certified closures.
This regression has no KRX API secret and no network calls.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from io import StringIO

from official_probe.clients import KRXProbeError
from v3_krx_session_challenge import inspect_days, SessionChallengeError, main

OPEN = "2026-09-23"
CLOSED = "2026-09-24"
CLOSED2 = "2026-09-25"
OPEN2 = "2026-09-28"


class MockProvider:
    def __init__(self,empty=None,wrong_date=None,explode=None):
        self.empty=set(empty or [])
        self.wrong_date=set(wrong_date or [])
        self.explode=set(explode or [])
        self.calls=[]
    def daily_market(self,market,day):
        self.calls.append((market,day))
        if (market,day) in self.explode:
            raise RuntimeError("AUTH_KEY=PRIVATE_TOKEN_NEVER_EMIT")
        if (market,day) in self.empty:
            raise KRXProbeError(market,"empty_market_rows",200)
        code="005930" if market=="KOSPI" else "247540"
        actual = "2026-09-22" if (market,day) in self.wrong_date else day
        rows=[{"BAS_DD":actual.replace("-",""),"ISU_CD":code,
               "TDD_OPNPRC":"100","TDD_HGPRC":"110",
               "TDD_LWPRC":"90","TDD_CLSPRC":"105","ACC_TRDVOL":"100"}]
        return rows,json.dumps({"OutBlock_1":rows}).encode()


class CalendarChallengeTests(TestCase):
    def test_closed_only_input_is_supported_without_any_api_request_in_dry_run(self):
        fake=MockProvider()
        report=inspect_days([CLOSED,CLOSED2],client=fake)
        self.assertEqual(report["third_party_excluded_dates"],[CLOSED,CLOSED2])
        self.assertEqual(report["third_party_session_candidate_dates"],[])
        self.assertEqual(report["actual_krx_market_requests"],0)
        self.assertEqual(fake.calls,[])
        self.assertFalse(report["official_calendar_whole_range_authenticated"])

    def test_real_KRX_two_market_date_observation_without_holiday_certification(self):
        fake=MockProvider(empty={("KOSPI",CLOSED),("KOSDAQ",CLOSED)})
        report=inspect_days([OPEN,CLOSED],live=True,key="SECRET",client=fake)
        self.assertEqual(report["observed_paired_date_count"],1)
        self.assertEqual(report["xkrx_excluded_dates_with_empty_market_rows"],1)
        self.assertFalse(report["calendar_challenge_discrepancy_or_error"])
        self.assertEqual(report["results"][0]["comparison"],"paired_date_observed_in_krx")
        self.assertEqual(report["results"][1]["comparison"],
                         "xkrx_exclusion_and_krx_empty_unverified")
        self.assertFalse(report["independent_krx_session_calendar_verified"])
        self.assertFalse(report["historical_250_sessions_verified"])
        self.assertNotIn("SECRET",json.dumps(report))
        self.assertNotIn("005930",json.dumps(report))

    def test_xkrx_closed_date_with_valid_two_market_KRX_observations_fails_open(self):
        fake=MockProvider()
        report=inspect_days([CLOSED],live=True,key="PRIVATE",client=fake)
        self.assertTrue(report["calendar_challenge_discrepancy_or_error"])
        self.assertEqual(report["results"][0]["comparison"],
                         "xkrx_excluded_day_has_two_market_observations")
        self.assertFalse(report["confirmed_scoring_allowed"])

    def test_open_day_with_both_empty_does_not_claim_official_closure(self):
        fake=MockProvider(empty={("KOSPI",OPEN),("KOSDAQ",OPEN)})
        report=inspect_days([OPEN],live=True,key="PRIVATE",client=fake)
        self.assertTrue(report["calendar_challenge_discrepancy_or_error"])
        self.assertEqual(report["results"][0]["comparison"],
                         "xkrx_session_but_two_market_empty_unverified")

    def test_one_market_empty_is_hard_discrepancy(self):
        fake=MockProvider(empty={("KOSDAQ",OPEN)})
        report=inspect_days([OPEN],live=True,key="KEY",client=fake)
        self.assertTrue(report["calendar_challenge_discrepancy_or_error"])
        self.assertEqual(report["results"][0]["comparison"],
                         "two_markets_disagree_or_partial_day")

    def test_stale_market_rows_and_vendor_errors_cannot_authenticate_day(self):
        for fake in (
            MockProvider(wrong_date={("KOSPI",OPEN)}),
            MockProvider(explode={("KOSDAQ",OPEN)}),
        ):
            with self.subTest(fake=fake):
                report=inspect_days([OPEN],live=True,key="TOKEN",client=fake)
                self.assertTrue(report["calendar_challenge_discrepancy_or_error"])
                self.assertEqual(report["results"][0]["comparison"],
                                 "source_error_or_unverified_response")
                text=json.dumps(report)
                self.assertNotIn("TOKEN",text)
                self.assertNotIn("AUTH_KEY",text)
                self.assertFalse(report["v3_release_ready"])

    def test_strict_batch_and_live_consent(self):
        with self.assertRaises(SessionChallengeError):
            inspect_days([OPEN,OPEN2],live=True,key="")
        with self.assertRaises(SessionChallengeError):
            inspect_days(["2026-09-17","2026-09-18","2026-09-21",
                          "2026-09-22","2026-09-23","2026-09-24"])

    def test_cli_writes_only_sanitized_private_manifest_and_returns_red_on_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            # CLI cannot write into arbitrary temporary paths; only ignored
            # in-repo evidence/krx_history summaries may be created.
            with patch("sys.stdout",new_callable=StringIO) as out:
                status=main(["--dates",CLOSED,"--output",str(Path(tmp)/"outside.json")])
            self.assertEqual(status,1)
            self.assertFalse((Path(tmp)/"outside.json").exists())
            self.assertIn("calendar_summary_location_not_private",out.getvalue())


if __name__=="__main__":
    import unittest
    unittest.main()
