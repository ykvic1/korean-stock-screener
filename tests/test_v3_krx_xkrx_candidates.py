"""Synthetic KRX API and versioned third-party XKRX candidate session tests.

No real KRX requests, credential access, raw-market artifacts or new trades.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v3_krx_session_candidates import candidate_dates, CandidateCalendarError
from v3_krx_private_backfill_plan import (
    plan_private_history, BackfillPlanError, calendar_weekday_candidates)
from v3_krx_local_resume_batch import run_one_local_batch
from v3_local_private_store import archived_trade_dates


class MockMarkets:
    def __init__(self):
        self.calls=[]
    def daily_market(self,market,asof):
        self.calls.append((market,asof))
        code="005930" if market=="KOSPI" else "247540"
        rows=[{"BAS_DD":asof.replace("-",""),"ISU_CD":code,
               "TDD_OPNPRC":"100","TDD_HGPRC":"110",
               "TDD_LWPRC":"90","TDD_CLSPRC":"103",
               "ACC_TRDVOL":"1000"}]
        return rows,json.dumps({"OutBlock_1":rows}).encode()


class CandidateSessionTests(unittest.TestCase):
    def test_weekdays_default_same_dates_but_no_official_assertion(self):
        w=calendar_weekday_candidates("2026-09-21","2026-09-30")
        c=candidate_dates(w,source="weekdays")
        self.assertEqual(c["dates"],w)
        self.assertEqual(c["excluded_weekdays_by_third_party_calendar"],0)
        self.assertFalse(c["official_krx_session_calendar_authenticated"])
        self.assertEqual(len(c["candidate_dates_sha256"]),64)

    def test_2026_chuseok_closures_are_only_third_party_candidates(self):
        weekdays=calendar_weekday_candidates("2026-09-21","2026-09-30")
        report=candidate_dates(weekdays,source="xkrx")
        self.assertEqual(report["dates"],[
            "2026-09-21","2026-09-22","2026-09-23",
            "2026-09-28","2026-09-29","2026-09-30"])
        self.assertEqual(report["excluded_weekdays_by_third_party_calendar"],2)
        self.assertFalse(report["official_krx_session_calendar_authenticated"])
        self.assertFalse(report["krx_ad_hoc_closure_or_future_revision_verified"])
        self.assertTrue(report["calendar_candidate_library_version"])

    def test_target_250_are_candidates_not_authenticated_exchange_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"secure"/"pairs.sqlite3"
            report=plan_private_history("2025-10-13","2026-10-16",
                        db_path=db,calendar_source="xkrx",batch_size=5)
            self.assertEqual(report["calendar_candidate_source"],"xkrx")
            self.assertEqual(report["calendar_candidate_count"],250)
            self.assertEqual(report["next_batch_dates"],[
                "2025-10-13","2025-10-14","2025-10-15",
                "2025-10-16","2025-10-17"])
            self.assertEqual(report["next_batch_max_calls"],10)
            self.assertFalse(report["historical_exchange_calendar_verified"])
            self.assertFalse(report["official_trading_days_250_verified"])
            self.assertFalse(report["confirmed_buy"])
            self.assertFalse(db.exists())

    def test_resume_across_third_party_closed_weekdays_never_requests_them(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"secure"/"pairs.sqlite3"
            fake=MockMarkets()
            report=run_one_local_batch(
                start="2026-09-22",end="2026-09-28",
                calendar_source="xkrx",database_path=db,
                live=True,consent=True,key="OFFLINE_TOKEN",client=fake)
            expected=["2026-09-22","2026-09-23","2026-09-28"]
            self.assertEqual(archived_trade_dates(database_path=db),expected)
            self.assertEqual(fake.calls,
                [(market,day) for day in expected for market in ("KOSPI","KOSDAQ")])
            self.assertEqual(report["new_paired_days_observed"],3)
            self.assertEqual(report["calendar_candidate_source"],"xkrx")
            self.assertFalse(report["official_krx_session_calendar_authenticated"])
            second=run_one_local_batch(
                start="2026-09-22",end="2026-09-28",
                calendar_source="xkrx",database_path=db,
                live=True,consent=True,key="OFFLINE_TOKEN",client=fake)
            self.assertEqual(second["actual_krx_calls"],0)
            self.assertEqual(len(fake.calls),6)

    def test_calendar_mixing_or_changed_archive_calendar_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"secure"/"pairs.sqlite3"
            fake=MockMarkets()
            run_one_local_batch(
                start="2026-09-22",end="2026-09-28",
                calendar_source="xkrx",database_path=db,
                live=True,consent=True,key="OFFLINE",client=fake)
            # The "weekdays" plan expects extra holiday observations which
            # cannot be inserted into an existing append-only hash chain.
            with self.assertRaisesRegex(BackfillPlanError,"archive_not_prefix"):
                plan_private_history("2026-09-22","2026-09-28",
                                     db_path=db,calendar_source="weekdays")
            self.assertEqual(len(fake.calls),6)

    def test_bad_calendar_selector_never_enters_live_mode(self):
        weekdays=calendar_weekday_candidates("2026-09-21","2026-09-23")
        with self.assertRaises(CandidateCalendarError):
            candidate_dates(weekdays,source="no_such_source")
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"secure"/"pairs.sqlite3"
            with self.assertRaises(BackfillPlanError):
                plan_private_history("2026-09-21","2026-09-23",
                                     db_path=db,calendar_source="bad")
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
