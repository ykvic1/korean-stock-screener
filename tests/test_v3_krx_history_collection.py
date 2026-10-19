"""Bounded real-KRX history integration tested with synthetic market responses only."""
from __future__ import annotations

from datetime import date, timedelta
from io import StringIO
from pathlib import Path
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from v3_krx_history_collection import (
    HistoryCollectionError, collect_krx_history,
    _snapshot_market, validate_dates, main, TARGET_PAIRED_DAYS,
)
from v3_local_private_store import inspect_private_store


def rows(market, day, invalid=False):
    code="005930" if market=="KOSPI" else "247540"
    data=[{
        "BAS_DD":day.replace("-",""),"ISU_CD":code,
        "TDD_OPNPRC":"100", "TDD_HGPRC":"120",
        "TDD_LWPRC":"90", "TDD_CLSPRC":"110",
        "ACC_TRDVOL":"10,000",
    }]
    if invalid:
        data.append({
            "BAS_DD":day.replace("-",""),
            "ISU_CD":"123456" if market=="KOSPI" else "234567",
            "TDD_OPNPRC":"-", "TDD_HGPRC":"-",
            "TDD_LWPRC":"-", "TDD_CLSPRC":"-",
            "ACC_TRDVOL":"0",
        })
    return data


class FakeClient:
    def __init__(self, bad=None, invalid=False):
        self.calls=[]
        self.bad=bad
        self.invalid=invalid
    def daily_market(self,market,day):
        self.calls.append((market,day))
        if self.bad == (market,day):
            raise RuntimeError("very-secret=NEVER-PRINT-ME")
        rec=rows(market,day,self.invalid)
        raw=json.dumps({"OutBlock_1":rec}).encode()
        return rec,raw


class KRXHistoricalCollectorTests(unittest.TestCase):
    def test_dry_run_requests_zero_api_calls_and_no_certification(self):
        provider=FakeClient()
        r=collect_krx_history(["2026-10-15","2026-10-16"],
                              client=provider)
        self.assertEqual(r["mode"],"dry_run")
        self.assertEqual(r["planned_market_calls"],4)
        self.assertEqual(provider.calls,[])
        self.assertFalse(r["verified_history_250_sessions"])
        self.assertFalse(r["confirmed_buy"])

    def test_two_market_per_day_complete_bounded_live_observed_not_certified(self):
        fake=FakeClient()
        out=collect_krx_history(["2026-10-14","2026-10-15","2026-10-16"],
                                live=True,key="LOCAL_SYNTHETIC",client=fake)
        self.assertEqual(out["observed_days"],3)
        self.assertEqual(len(fake.calls),6)
        self.assertEqual(out["target_paired_trading_sessions"],250)
        self.assertEqual([x["asof"] for x in out["days"]],
                         ["2026-10-14","2026-10-15","2026-10-16"])
        for daily in out["days"]:
            self.assertEqual([m["market"] for m in daily["markets"]],["KOSPI","KOSDAQ"])
            for item in daily["markets"]:
                self.assertEqual(item["provider_rows_observed"],1)
                self.assertEqual(item["observed_numeric_candidates"],1)
                self.assertFalse(item["regular_session_volume_verified"])
                self.assertFalse(item["confirmed_scoring_allowed"])
                self.assertEqual(len(item["source_sha256"]),64)
        serialized=json.dumps(out)
        self.assertNotIn("LOCAL_SYNTHETIC",serialized)
        self.assertNotIn("ACC_TRDVOL",serialized)
        self.assertFalse(out["verified_history_250_sessions"])
        self.assertFalse(out["v3_release_ready"])

    def test_unpriced_and_suspended_rows_are_not_filled(self):
        fake=FakeClient(invalid=True)
        r=collect_krx_history(["2026-10-16"],live=True,key="K",client=fake)
        self.assertEqual(r["days"][0]["markets"][0]["provider_rows_observed"],2)
        self.assertEqual(r["days"][0]["markets"][0]["observed_numeric_candidates"],1)
        self.assertEqual(r["days"][0]["markets"][0]["quarantined_unpriced_candidates"],1)
        self.assertFalse(r["historical_full_universe_verified"])

    def test_duplicate_code_and_wrong_issuer_date_block_batch(self):
        day="2026-10-16"
        row=rows("KOSPI",day)[0]
        raw=b'{"secret":"not-returned"}'
        for malformed in ([row,row],
                          [{**row,"BAS_DD":"20261015"}],
                          [{**row,"ISU_CD":"005930000000"}]):
            with self.subTest(malformed=malformed),self.assertRaises(HistoryCollectionError):
                _snapshot_market(malformed,raw,market="KOSPI",asof=day)

    def test_market_data_must_match_exact_original_response_bytes(self):
        day="2026-10-16"
        original=rows("KOSPI",day)
        raw=json.dumps({"OutBlock_1":original}).encode()
        malicious=copy.deepcopy(original)
        malicious[0]["TDD_CLSPRC"]="115"
        with self.assertRaisesRegex(HistoryCollectionError,"disagree_with_raw"):
            _snapshot_market(malicious,raw,market="KOSPI",asof=day)

    def test_all_suspended_rows_cannot_confirm_market(self):
        batch=rows("KOSPI","2026-10-16",True)[1:]
        with self.assertRaisesRegex(HistoryCollectionError,"no_usable"):
            _snapshot_market(batch,json.dumps({"OutBlock_1":batch}).encode(),
                             market="KOSPI",asof="2026-10-16")

    def test_invalid_dates_weekend_unsorted_and_oversized_batches_rejected(self):
        for days in ([],["2026-10-16"]*2,["2026-10-17"],
                     ["2026-10-16","2026-10-15"],
                     ["2026-10-20"], ["2026-10-14"]*6,
                     ["2026-02-30"]):
            with self.subTest(days=days),self.assertRaises(HistoryCollectionError):
                validate_dates(days)

    def test_staging_failure_cannot_store_half_market_pair(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"private-v3"/"pairs.sqlite3"
            fake=FakeClient(bad=("KOSDAQ","2026-10-16"))
            with self.assertRaises(HistoryCollectionError):
                collect_krx_history(["2026-10-16"],live=True,key="SECRET",
                                    client=fake,archive_path=db,
                                    consent_to_private_retention=True)
            self.assertFalse(db.exists())
            self.assertEqual(fake.calls,[("KOSPI","2026-10-16"),
                                          ("KOSDAQ","2026-10-16")])

    def test_success_private_pair_local_only_when_explicit_consent(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"private"/"series.sqlite3"
            with self.assertRaisesRegex(HistoryCollectionError,"consent"):
                collect_krx_history(["2026-10-16"],live=True,key="KEY",
                    client=FakeClient(),archive_path=db)
            self.assertFalse(db.exists())
            r=collect_krx_history(["2026-10-16"],live=True,key="KEY",
                     client=FakeClient(),archive_path=db,
                     consent_to_private_retention=True)
            self.assertTrue(r["days"][0]["stored_in_private_sqlite"])
            scan=inspect_private_store(database_path=db)
            self.assertEqual(scan["archived_days"],1)
            self.assertFalse(scan["authorized_for_v3_score"])
            self.assertFalse(scan["private_at_rest_encryption_provided"])

    def test_prior_complete_day_kept_if_later_market_fails_no_fake_batch(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"private"/"series.sqlite3"
            fake=FakeClient(bad=("KOSDAQ","2026-10-16"))
            with self.assertRaises(HistoryCollectionError):
                collect_krx_history(["2026-10-15","2026-10-16"],
                    live=True,key="KEY",client=fake,archive_path=db,
                    consent_to_private_retention=True)
            self.assertEqual(inspect_private_store(database_path=db)["archived_days"],1)

    def test_key_missing_and_public_repo_db_forbidden(self):
        with self.assertRaisesRegex(HistoryCollectionError,"key_required"):
            collect_krx_history(["2026-10-16"],live=True,key="")
        p=Path(__file__).resolve().parents[1]/"docs"/"forbidden.sqlite3"
        with self.assertRaises(HistoryCollectionError):
            collect_krx_history(["2026-10-16"],live=True,key="KEY",
                                client=FakeClient(),archive_path=p,
                                consent_to_private_retention=True)
        self.assertFalse(p.exists())

    def test_cli_dry_run_summary_only_allowlisted_private_path(self):
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/"report.json"
            with patch("sys.stdout",new_callable=StringIO):
                rc=main(["--dates","2026-10-15,2026-10-16","--output",str(output)])
            self.assertEqual(rc,0)
            self.assertEqual(json.loads(output.read_text())["planned_market_calls"],4)
            with patch("sys.stdout",new_callable=StringIO):
                rc=main(["--dates","2026-10-15","--private-db",str(Path(td)/"x.sqlite3")])
            self.assertEqual(rc,1)


if __name__ == "__main__":
    unittest.main()
