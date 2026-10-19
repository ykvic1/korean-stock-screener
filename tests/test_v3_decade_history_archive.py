"""No-network decade-plus backfill and history-capacity regressions.

20/60/120/250 are factor lookbacks, NEVER archival cut-offs.
XKRX is only a third-party trading-day candidate (not official proof).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json
import tempfile
import unittest

from v3_krx_private_backfill_plan import (
    calendar_weekday_candidates, plan_private_history,
    plan_next_batch, BackfillPlanError, MAX_DAILY_CALLS,
    MAX_PLANNED_WEEKDAYS, MAX_PLANNED_CALENDAR_DAYS)
from v3_krx_session_candidates import candidate_dates
from v3_evidence_chain import (MAX_RECORDS, ZERO_HASH, empty_manifest,
                               verify_manifest, _entry_content, _hash)
from v3_local_private_store import store_pair, inspect_private_store
from v3_price_feature_adapter import MAX_BARS, _validated


class DecadeArchiveResearchTests(unittest.TestCase):
    def test_10_year_rolling_research_is_not_cut_to_250_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private" / "decade.sqlite3"
            report = plan_private_history(
                "2016-10-17", "2026-10-16", db_path=path,
                batch_size=5, calendar_source="xkrx")
            self.assertGreater(report["calendar_candidate_count"], 2300)
            self.assertLess(report["calendar_candidate_count"], 2700)
            self.assertEqual(report["next_batch_max_calls"], MAX_DAILY_CALLS)
            self.assertEqual(len(report["next_batch_dates"]), 5)
            self.assertEqual(
                report["remaining_weekday_candidates"],
                report["calendar_candidate_count"])
            self.assertFalse(report["official_trading_days_250_verified"])
            self.assertFalse(report["historical_exchange_calendar_verified"])
            self.assertFalse(report["v3_release_ready"])
            self.assertFalse(path.exists())
            self.assertGreater(MAX_PLANNED_WEEKDAYS, 3000)

    def test_older_research_since_2010_is_not_silently_truncated(self):
        start, end = "2010-01-04", "2026-10-16"
        dates = calendar_weekday_candidates(start, end)
        self.assertGreater(len(dates), 4000)
        self.assertLess(len(dates), MAX_PLANNED_WEEKDAYS)
        self.assertLess((date.fromisoformat(end)-date.fromisoformat(start)).days,
                        MAX_PLANNED_CALENDAR_DAYS)
        self.assertGreater(MAX_RECORDS, len(dates))
        next_chunk=plan_next_batch(dates, batch_size=5)
        self.assertEqual(next_chunk["next_batch_dates"], dates[:5])
        self.assertEqual(next_chunk["remaining_weekday_candidates"], len(dates))
        self.assertEqual(next_chunk["next_batch_max_calls"], 10)

    def test_10_year_resume_preserves_full_prefix_and_five_date_call_ceiling(self):
        weekdays=calendar_weekday_candidates("2016-10-17", "2026-10-16")
        candidates=candidate_dates(weekdays, source="xkrx")["dates"]
        plan=plan_next_batch(candidates,already_archived=candidates[:250],
                             batch_size=5)
        self.assertEqual(plan["archived_two_market_pairs_verified_internally"],250)
        self.assertEqual(plan["next_batch_dates"],candidates[250:255])
        self.assertEqual(plan["remaining_weekday_candidates"],len(candidates)-250)
        self.assertEqual(plan["next_batch_max_calls"],10)
        self.assertFalse(plan["v3_release_ready"])
        # Backdating an existing 250-day-only archive cannot rewrite its
        # chronology: migrate/rebuild in a distinct private database instead.
        with self.assertRaisesRegex(
                BackfillPlanError,"archive_not_prefix_of_candidate_sequence"):
            plan_next_batch(candidates,already_archived=candidates[200:450])

    def test_new_database_required_for_prepend_before_existing_recent_archive(self):
        day="2025-10-13"
        def raw(code):
            return json.dumps({"OutBlock_1":[{
                "BAS_DD":"20251013","ISU_CD":code,
                "TDD_OPNPRC":"101","TDD_HGPRC":"105",
                "TDD_LWPRC":"99","TDD_CLSPRC":"103",
                "ACC_TRDVOL":"500"}]}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"private"/"market.sqlite3"
            store_pair(
                database_path=db, asof=day,
                kospi_raw=raw("005930"), kosdaq_raw=raw("247540"),
                kospi_received_at_utc=day+"T07:45:00Z",
                kosdaq_received_at_utc=day+"T07:45:01Z")
            self.assertEqual(inspect_private_store(database_path=db)["archived_days"],1)
            with self.assertRaisesRegex(BackfillPlanError,"archive_not_prefix"):
                plan_private_history("2016-10-17","2026-10-16",
                                     db_path=db,calendar_source="xkrx")
            self.assertEqual(inspect_private_store(database_path=db)["archived_days"],1)

    def test_long_manifest_over_previous_1800_day_limit_is_valid(self):
        manifest=empty_manifest()
        prev=ZERO_HASH
        d=date(2016,1,4)
        n=0
        while n<1901:
            if d.weekday()<5:
                day=d.isoformat()
                sources={
                    "KOSPI":{"raw_sha256":"a"*64,"byte_count":100,
                              "received_at_utc":day+"T07:30:00Z"},
                    "KOSDAQ":{"raw_sha256":"b"*64,"byte_count":200,
                               "received_at_utc":day+"T07:31:00Z"},
                }
                item=_entry_content(day,sources,prev)
                digest=_hash(item)
                manifest["entries"].append({**item,"entry_hash":digest})
                prev=digest
                n+=1
            d+=timedelta(days=1)
        checked=verify_manifest(manifest)
        self.assertEqual(checked["records"],1901)
        self.assertTrue(checked["internal_linkage_consistent"])
        self.assertFalse(checked["independent_head_anchor_verified"])
        self.assertFalse(checked["v3_release_ready"])

    def test_daily_feature_reader_does_not_cap_series_at_4000(self):
        self.assertGreaterEqual(MAX_BARS,5000)
        dates=calendar_weekday_candidates("2010-01-04","2026-10-16")[:4100]
        bars=[{"date":day,"session":"KRX_REGULAR_0900_1530_ASIA_SEOUL",
               "open":100,"high":110,"low":90,"close":101,"volume":200}
              for day in dates]
        df=_validated(bars,asof=dates[-1])
        self.assertEqual(len(df),4100)
        self.assertEqual(float(df.Close.iloc[0]),101.0)

    def test_single_batch_stays_bounded_and_future_dates_rejected(self):
        dates=calendar_weekday_candidates("2016-10-17","2026-10-16")
        plan=plan_next_batch(dates,batch_size=5)
        self.assertEqual(plan["next_batch_max_calls"],10)
        with self.assertRaisesRegex(BackfillPlanError,"invalid_plan_size"):
            plan_next_batch(dates,batch_size=6)
        with self.assertRaises(BackfillPlanError):
            calendar_weekday_candidates("1990-01-01","2026-10-16")
        with self.assertRaises(BackfillPlanError):
            calendar_weekday_candidates("2026-10-20","2026-10-21")


if __name__=="__main__":
    unittest.main()
