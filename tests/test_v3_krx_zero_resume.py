"""Offline tests for field-level zero OHLC and consent-gated KRX resume."""
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from io import StringIO

from v3_krx_zero_price_detail import (
    empty_zero_details,observe_zero_price_row,validate_zero_details)
from v3_krx_history_collection import _snapshot_market
from v3_krx_local_resume_batch import (
    run_one_local_batch,LocalBatchError,main)
from v3_local_private_store import (
    archived_trade_dates,inspect_private_store)

DAY0="2026-10-15"
DAY1="2026-10-16"


def example_row(code, day, *, open="100", high="110", low="90",
                close="102",volume="100"):
    return {"ISU_CD":code,"BAS_DD":day.replace("-",""),
            "TDD_OPNPRC":open,"TDD_HGPRC":high,
            "TDD_LWPRC":low,"TDD_CLSPRC":close,
            "ACC_TRDVOL":volume}


class SyntheticKRX:
    def __init__(self,bad=None):
        self.calls=[]
        self.bad=bad
    def daily_market(self,market,day):
        self.calls.append((market,day))
        if self.bad == (market,day):
            raise RuntimeError("AUTH_KEY=MUST_NOT_LEAK")
        code="005930" if market=="KOSPI" else "247540"
        one=example_row(code,day)
        raw=json.dumps({"OutBlock_1":[one]}).encode()
        return [one],raw


class ZeroOHLCFieldEvidenceTests(unittest.TestCase):
    def test_cross_field_zero_and_volume_count_are_distinct_and_complete(self):
        date=DAY0
        cases=[
            example_row("005930",date),
            example_row("00088K",date,open="0",close="0",volume="0"),
            example_row("00088L",date,low="0",volume="7"),
            example_row("00088M",date,high="0",volume="-"),
            example_row("00088N",date,close="0",volume="0"),
        ]
        audited=_snapshot_market(
            cases,json.dumps({"OutBlock_1":cases}).encode(),
            market="KOSPI",asof=date)
        self.assertEqual(audited["provider_rows_observed"],5)
        self.assertEqual(audited["quarantined_unpriced_candidates"],4)
        count=audited["zero_price_ohlc_field_counts"]
        self.assertEqual(count["zero_price_rows"],4)
        self.assertEqual(count["zero_open_rows"],1)
        self.assertEqual(count["zero_high_rows"],1)
        self.assertEqual(count["zero_low_rows"],1)
        self.assertEqual(count["zero_close_rows"],2)
        self.assertEqual(count["multiple_zero_ohlc_rows"],1)
        self.assertEqual(count["zero_price_with_zero_accumulated_volume"],2)
        self.assertEqual(count["zero_price_with_positive_accumulated_volume"],1)
        self.assertEqual(count["zero_price_with_unparseable_accumulated_volume"],1)
        self.assertFalse(audited["confirmed_scoring_allowed"])
        self.assertNotIn("00088K",json.dumps(audited))
        self.assertNotIn("100",json.dumps(audited.get("zero_price_ohlc_field_counts")))

    def test_no_zero_reason_can_create_zero_detail_without_source_zero(self):
        with self.assertRaisesRegex(ValueError,"without_zero"):
            observe_zero_price_row(example_row("005930",DAY0),empty_zero_details())

    def test_invalid_detail_counts_fail_closed(self):
        d=empty_zero_details()
        d["zero_price_rows"]=1
        d["zero_open_rows"]=1
        d["zero_price_with_positive_accumulated_volume"]=1
        self.assertTrue(validate_zero_details(d,1))
        for k,v in (("zero_open_rows",2),
                    ("zero_price_with_positive_accumulated_volume",0),
                    ("multiple_zero_ohlc_rows",-1)):
            tampered=dict(d)
            tampered[k]=v
            with self.subTest(field=k),self.assertRaises(ValueError):
                validate_zero_details(tampered,1)


class SafeResumeLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"private"/"pairs.sqlite3"

    def test_offline_default_zero_calls_no_storage(self):
        provider=SyntheticKRX()
        plan=run_one_local_batch(start=DAY0,end=DAY1,
                 database_path=self.db,client=provider)
        self.assertEqual(provider.calls,[])
        self.assertFalse(self.db.exists())
        self.assertEqual(plan["next_batch_dates"],[DAY0,DAY1])
        self.assertEqual(plan["actual_krx_calls"],0)
        self.assertFalse(plan["v3_release_ready"])

    def test_consent_and_key_required_before_any_live(self):
        provider=SyntheticKRX()
        for kwargs,reason in [
            (dict(live=True,consent=False,key="KEY"),
             "private_storage_contract_ack_required"),
            (dict(live=True,consent=True,key=None),
             "krx_api_key_environment_missing"),
        ]:
            with self.subTest(reason=reason),self.assertRaisesRegex(
                    LocalBatchError,reason):
                run_one_local_batch(start=DAY0,end=DAY1,
                    database_path=self.db,client=provider,**kwargs)
            self.assertEqual(provider.calls,[])
            self.assertFalse(self.db.exists())

    def test_live_two_market_pairs_saved_and_resume_zero_extra_calls(self):
        fake=SyntheticKRX()
        report=run_one_local_batch(start=DAY0,end=DAY1,database_path=self.db,
            live=True,consent=True,key="TEST_SECRET",client=fake)
        self.assertEqual(fake.calls,[
            ("KOSPI",DAY0),("KOSDAQ",DAY0),
            ("KOSPI",DAY1),("KOSDAQ",DAY1)])
        self.assertEqual(report["new_paired_days_observed"],2)
        self.assertEqual(report["total_archive_paired_days_integrity_checked"],2)
        self.assertFalse(report["regular_only_volume_verified"])
        self.assertFalse(report["v3_release_ready"])
        self.assertEqual(archived_trade_dates(database_path=self.db),[DAY0,DAY1])
        second=run_one_local_batch(start=DAY0,end=DAY1,database_path=self.db,
            live=True,consent=True,key="TEST_SECRET",client=fake)
        self.assertEqual(second["mode"],"live_no_missing_candidate_dates")
        self.assertEqual(second["actual_krx_calls"],0)
        self.assertEqual(len(fake.calls),4)
        self.assertFalse(second["confirmed_buy"])

    def test_partial_failure_keeps_only_complete_pair_and_resume_continues(self):
        fake=SyntheticKRX(bad=("KOSDAQ",DAY1))
        with self.assertRaisesRegex(LocalBatchError,"local_batch_incomplete"):
            run_one_local_batch(start=DAY0,end=DAY1,database_path=self.db,
                live=True,consent=True,key="KEY",client=fake)
        self.assertEqual(archived_trade_dates(database_path=self.db),[DAY0])
        again=SyntheticKRX()
        report=run_one_local_batch(start=DAY0,end=DAY1,database_path=self.db,
            live=True,consent=True,key="KEY",client=again)
        self.assertEqual(again.calls,[("KOSPI",DAY1),("KOSDAQ",DAY1)])
        self.assertEqual(report["total_archive_paired_days_integrity_checked"],2)

    def test_reject_public_repo_storage_without_leaking_token(self):
        repo=Path(__file__).resolve().parents[1]
        path=repo/"docs"/"cannot-write-historical.sqlite3"
        provider=SyntheticKRX()
        with self.assertRaisesRegex(LocalBatchError,"private_storage_path_not_safe"):
            run_one_local_batch(start=DAY0,end=DAY1,database_path=path,
                live=True,consent=True,key="VERY_PRIVATE",client=provider)
        self.assertEqual(provider.calls,[])
        self.assertFalse(path.exists())

    def test_cli_no_key_exposed_and_no_implicit_storage_path(self):
        with patch("sys.stdout",new_callable=StringIO) as output:
            ret=main(["--start",DAY0,"--end",DAY1,
                      "--live","--ack-private-storage"])
        self.assertEqual(ret,1)
        self.assertIn("explicit_private_database_path_required",output.getvalue())
        self.assertNotIn("AUTH_KEY",output.getvalue())


if __name__=="__main__":
    unittest.main()
