"""All synthetic/no-network: KRX price quarantine categories and private resume."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from v3_krx_daily_anomaly_triage import classify_row, validate_quality_counts, new_counts
from v3_krx_history_collection import _snapshot_market
from v3_krx_private_backfill_plan import (
    calendar_weekday_candidates, plan_next_batch, plan_private_history,
    BackfillPlanError)
from v3_local_private_store import (store_pair, inspect_private_store,
                                    archived_trade_dates)

DAY="2026-10-15"
STAMP="2026-10-15T07:10:00Z"


def row(code="005930"):
    return {
        "ISU_CD":code,"BAS_DD":"20261015",
        "TDD_OPNPRC":"100","TDD_HGPRC":"110",
        "TDD_LWPRC":"90","TDD_CLSPRC":"103",
        "ACC_TRDVOL":"1000",
    }


class DailyAnomalyTests(unittest.TestCase):
    def test_each_reason_is_numeric_source_category_not_market_cause(self):
        base=row()
        variants=[
            ({"TDD_CLSPRC":"-"},"missing_price_field"),
            ({"TDD_OPNPRC":None},"missing_price_field"),
            ({"TDD_HGPRC":"abc"},"non_numeric_price"),
            ({"TDD_LWPRC":"0"},"zero_or_negative_price"),
            ({"TDD_CLSPRC":"-4"},"non_numeric_price"),
            ({"TDD_HGPRC":"101"},"ohlc_range_inconsistent"),
            ({"ACC_TRDVOL":"-"},"missing_volume_field"),
            ({"ACC_TRDVOL":"xyz"},"non_numeric_volume"),
        ]
        for changes,expected in variants:
            with self.subTest(expected=expected,values=changes):
                category,is_zero=classify_row({**base,**changes})
                self.assertEqual(category,expected)
                self.assertFalse(is_zero)

    def test_zero_aggregate_volume_is_not_a_trading_halt(self):
        self.assertEqual(classify_row({**row(),"ACC_TRDVOL":"0"}),
                         ("valid_numeric_observation",True))
        self.assertEqual(classify_row(row()),
                         ("valid_numeric_observation",False))

    def test_aggregate_accounting_and_source_identity(self):
        cases=[row("005930"),
               {**row("00088K"),"TDD_CLSPRC":"-"},
               {**row("00088L"),"TDD_HGPRC":"105"},
               {**row("00088M"),"TDD_OPNPRC":"0"},
               {**row("00088N"),"ACC_TRDVOL":"N/A"},
               {**row("00088P"),"ACC_TRDVOL":"0"},
               {**row("00088Q"),"TDD_CLSPRC":"-"}]
        raw=json.dumps({"OutBlock_1":cases}).encode()
        safe=_snapshot_market(cases,raw,market="KOSPI",asof=DAY)
        self.assertEqual(safe["provider_rows_observed"],7)
        self.assertEqual(safe["observed_numeric_candidates"],2)
        self.assertEqual(safe["quarantined_unpriced_candidates"],5)
        self.assertEqual(safe["zero_accumulated_volume_on_numeric_rows"],1)
        self.assertEqual(safe["price_volume_anomaly_reason_counts"]["missing_price_field"],2)
        self.assertEqual(safe["price_volume_anomaly_reason_counts"]["ohlc_range_inconsistent"],1)
        self.assertEqual(safe["price_volume_anomaly_reason_counts"]["zero_or_negative_price"],1)
        self.assertEqual(safe["price_volume_anomaly_reason_counts"]["non_numeric_volume"],1)
        self.assertEqual(safe["price_volume_anomaly_reason_counts"]["valid_numeric_observation"],2)
        self.assertFalse(safe["halt_or_delisting_cause_verified"])
        self.assertFalse(safe["corporate_actions_verified"])
        self.assertFalse(safe["confirmed_scoring_allowed"])
        serial=json.dumps(safe)
        for code in ("005930","00088K","00088L","00088P"):
            self.assertNotIn(code,serial)

    def test_counts_cannot_be_faked_or_inconsistent(self):
        a=new_counts()
        a["valid_numeric_observation"]=2
        self.assertTrue(validate_quality_counts(a,2,0))
        with self.assertRaises(ValueError):
            validate_quality_counts(a,5,0)
        with self.assertRaises(ValueError):
            validate_quality_counts(a,2,3)
        with self.assertRaises(ValueError):
            validate_quality_counts({"valid_numeric_observation":2},2,0)


class AppendOnlyPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"private"/"history.sqlite3"

    def store(self,day,receipt,one=b'{"market":"KPI"}',two=b'{"market":"KDQ"}'):
        return store_pair(database_path=self.db,asof=day,
                          kospi_raw=one,kosdaq_raw=two,
                          kospi_received_at_utc=receipt,
                          kosdaq_received_at_utc=receipt)

    def test_empty_db_plans_five_day_pairs_without_creating_storage(self):
        plan=plan_private_history("2026-10-12","2026-10-16",
                                  db_path=self.db,batch_size=5)
        self.assertEqual(plan["next_batch_dates"],
                         ["2026-10-12","2026-10-13","2026-10-14",
                          "2026-10-15","2026-10-16"])
        self.assertEqual(plan["next_batch_max_calls"],10)
        self.assertFalse(self.db.exists())
        self.assertFalse(plan["historical_exchange_calendar_verified"])
        self.assertFalse(plan["official_trading_days_250_verified"])
        self.assertFalse(plan["v3_release_ready"])

    def test_resume_prefix_skips_archived_first_day_without_requery(self):
        self.store(DAY,STAMP)
        report=plan_private_history(DAY,"2026-10-16",db_path=self.db)
        self.assertEqual(archived_trade_dates(database_path=self.db),[DAY])
        self.assertEqual(report["archived_two_market_pairs_verified_internally"],1)
        self.assertEqual(report["next_batch_dates"],["2026-10-16"])
        self.assertFalse(report["confirmed_buy"])
        self.assertEqual(inspect_private_store(database_path=self.db)["archived_days"],1)

    def test_archive_with_gap_or_older_prepend_does_not_silently_resume(self):
        self.store("2026-10-16","2026-10-16T07:10:00Z")
        with self.assertRaisesRegex(BackfillPlanError,"archive_not_prefix"):
            plan_private_history("2026-10-14","2026-10-16",db_path=self.db)

    def test_corrupted_raw_source_cannot_be_planned(self):
        self.store(DAY,STAMP)
        conn=sqlite3.connect(self.db)
        conn.execute("UPDATE source_pairs SET kospi_raw=?", (b'compromised',))
        conn.commit();conn.close()
        with self.assertRaisesRegex(BackfillPlanError,"integrity"):
            plan_private_history(DAY,"2026-10-16",db_path=self.db)

    def test_same_file_can_be_resumed_only_after_valid_pair_append(self):
        self.store(DAY,STAMP)
        self.store("2026-10-16","2026-10-16T07:10:00Z",b'{"market":"KPI2"}',b'{"market":"KDQ2"}')
        plan=plan_private_history(DAY,"2026-10-16",db_path=self.db)
        self.assertEqual(plan["next_batch_dates"],[])
        self.assertFalse(plan["official_trading_days_250_verified"])

    def test_250_weekdays_is_never_called_250_verified_sessions(self):
        dates=calendar_weekday_candidates("2025-10-01","2026-10-16")
        self.assertGreater(len(dates),250)
        result=plan_next_batch(dates,batch_size=5)
        self.assertEqual(result["next_batch_dates"],dates[:5])
        self.assertEqual(result["next_batch_max_calls"],10)
        self.assertFalse(result["historical_exchange_calendar_verified"])
        self.assertFalse(result["official_trading_days_250_verified"])

    def test_wrong_schedule_and_invalid_limits_fail_closed(self):
        for start,end in (("2026-10-17","2026-10-18"),
                          ("2026-10-16","2026-10-15"),
                          ("2025-01-01","2026-10-16")):
            with self.subTest(start=start,end=end),self.assertRaises(BackfillPlanError):
                calendar_weekday_candidates(start,end)
        with self.assertRaises(BackfillPlanError):
            plan_next_batch(["2026-10-16","2026-10-15"])
        with self.assertRaises(BackfillPlanError):
            plan_next_batch(["2026-10-15"]*2)
        with self.assertRaises(BackfillPlanError):
            plan_next_batch(["2026-10-15"],batch_size=6)

if __name__=="__main__":
    unittest.main()
