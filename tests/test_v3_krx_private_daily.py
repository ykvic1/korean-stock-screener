"""No-network tests for approved-later KRX two-market private daily store."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import v3_krx_private_daily as p


ASOF = "2026-10-16"


def stock(code, *, asof="20261016", price="100", volume="1,250"):
    return {"ISU_CD": code, "BAS_DD": asof,
            "TDD_OPNPRC": price, "TDD_HGPRC": price,
            "TDD_LWPRC": price, "TDD_CLSPRC": price,
            "ACC_TRDVOL": volume}


def fake_client(*, kospi=None, kosdaq=None, failing=None):
    class FakeKRX:
        def __init__(self, key):
            if key != "SYNTHETIC_PRIVATE_API_KEY":
                raise AssertionError("unexpected credential")
            self.calls = []

        def daily_market(self, market, asof):
            self.calls.append((market, asof))
            if market == failing:
                raise RuntimeError("SENSITIVE_REMOTE_ERR_AUTH_KEY=NEVER_LOG")
            rows = (deepcopy(kospi) if market == "KOSPI" else deepcopy(kosdaq))
            return rows, (market + "-SYNTHETIC-MARKET-RESPONSE").encode()
    return FakeKRX


DEFAULT_KOSPI = [stock("005930"), stock("000660")]
DEFAULT_KOSDAQ = [stock("252990"), stock("035720")]


class PrivateKrxDailyTests(unittest.TestCase):
    def test_default_dry_run_does_not_need_creds_or_network(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = p.main(["--asof", ASOF])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(buffer.getvalue())["status"], "not_run")

    def test_live_without_key_fails_before_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(p.IngestError, "missing_KRX_API_KEY"):
                p.ingest(asof=ASOF, key="", private_dir=Path(tmp),
                         client_factory=lambda _: 1, minimum_stocks=1)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_reject_future_impossible_and_wrong_date_format(self):
        for bad in ("2099-01-01", "2026-02-30", "20261016", "2026-10-161", None):
            with self.subTest(bad=bad), self.assertRaises(p.IngestError):
                p._date(bad)

    def test_two_market_success_only_private_disk(self):
        creator = fake_client(kospi=DEFAULT_KOSPI, kosdaq=DEFAULT_KOSDAQ)
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / "safe-research"
            report = p.ingest(asof=ASOF, key="SYNTHETIC_PRIVATE_API_KEY",
                              private_dir=private, client_factory=creator,
                              minimum_stocks=2)
            self.assertEqual(report["status"], "new_private_snapshot_written")
            self.assertEqual(report["kospi_rows"], 2)
            self.assertEqual(report["kosdaq_rows"], 2)
            self.assertFalse(report["confirmed_scoring_allowed"])
            self.assertFalse(report["investor_flow_confirmed"])
            saved = json.loads((private / "krx_20261016.json").read_text(encoding="utf-8"))
            self.assertEqual(set(saved["markets"]), set(p.MARKETS))
            self.assertEqual(saved["markets"]["KOSPI"]["ohlcv"][0]["code"], "005930")
            self.assertEqual(saved["markets"]["KOSPI"]["ohlcv"][0]["accumulated_volume_observed"], 1250)
            self.assertIsNone(saved["markets"]["KOSPI"]["ohlcv"][0]["regular_only_volume"])
            self.assertFalse(saved["official_regular_session_volume_verified"])
            self.assertFalse(saved["historical_120_250_day_continuity_verified"])
            self.assertFalse(saved["investor_category_flows_included"])
            self.assertNotIn("SYNTHETIC_PRIVATE_API_KEY", json.dumps(saved))
            self.assertNotIn("SENSITIVE_REMOTE", json.dumps(saved))
            if os.name == "posix":
                self.assertEqual((private / "krx_20261016.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual(private.stat().st_mode & 0o777, 0o700)

    def test_same_immutable_snapshot_does_not_overwrite(self):
        factory = fake_client(kospi=DEFAULT_KOSPI, kosdaq=DEFAULT_KOSDAQ)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = p.ingest(asof=ASOF, key="SYNTHETIC_PRIVATE_API_KEY",
                             private_dir=folder, client_factory=factory,
                             minimum_stocks=2)
            previous = (folder / "krx_20261016.json").read_bytes()
            second = p.ingest(asof=ASOF, key="SYNTHETIC_PRIVATE_API_KEY",
                              private_dir=folder, client_factory=factory,
                              minimum_stocks=2)
            self.assertEqual(first["status"], "new_private_snapshot_written")
            self.assertEqual(second["status"], "already_recorded_unchanged")
            self.assertEqual((folder / "krx_20261016.json").read_bytes(), previous)

    def test_same_day_different_vendor_result_is_audit_conflict(self):
        factory = fake_client(kospi=DEFAULT_KOSPI, kosdaq=DEFAULT_KOSDAQ)
        modified = deepcopy(DEFAULT_KOSPI)
        modified[0]["TDD_CLSPRC"] = "101"
        modified[0]["TDD_HGPRC"] = "101"
        later = fake_client(kospi=modified, kosdaq=DEFAULT_KOSDAQ)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p.ingest(asof=ASOF, key="SYNTHETIC_PRIVATE_API_KEY",
                     private_dir=folder, client_factory=factory,
                     minimum_stocks=2)
            previous = (folder / "krx_20261016.json").read_bytes()
            with self.assertRaisesRegex(p.IngestError, "existing_immutable_snapshot_conflict"):
                p.ingest(asof=ASOF, key="SYNTHETIC_PRIVATE_API_KEY",
                         private_dir=folder, client_factory=later,
                         minimum_stocks=2)
            self.assertEqual((folder / "krx_20261016.json").read_bytes(), previous)

    def test_api_second_market_failure_cannot_write_half_day(self):
        factory = fake_client(kospi=DEFAULT_KOSPI, kosdaq=DEFAULT_KOSDAQ, failing="KOSDAQ")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(p.IngestError, "unexpected_provider_failure"):
                p.ingest(asof=ASOF, key="SYNTHETIC_PRIVATE_API_KEY",
                         private_dir=Path(tmp), client_factory=factory,
                         minimum_stocks=2)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_duplicate_code_within_market(self):
        rows = [stock("005930"), stock("005930")]
        with self.assertRaisesRegex(p.IngestError, "duplicate_market_code"):
            p.normalize_market(rows,market="KOSPI",asof=ASOF,source=b"fake",minimum_stocks=2)

    def test_duplicate_cross_market_fails_before_private_write(self):
        factory = fake_client(kospi=[stock("005930")],kosdaq=[stock("005930")])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(p.IngestError, "duplicate_cross_market_code"):
                p.ingest(asof=ASOF,key="SYNTHETIC_PRIVATE_API_KEY",
                         private_dir=Path(tmp),client_factory=factory,minimum_stocks=1)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_date_or_price_is_not_zero(self):
        for field,value in (("BAS_DD","20261015"),("TDD_CLSPRC","-"),
                            ("TDD_CLSPRC",None),("TDD_HGPRC","95"),
                            ("ACC_TRDVOL","-"),("ACC_TRDVOL","-10")):
            rows=[stock("005930"),stock("000660")]
            rows[0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(p.IngestError):
                p.normalize_market(rows,market="KOSPI",asof=ASOF,source=b"fake",minimum_stocks=2)

    def test_explicit_no_trade_allowed_but_not_a_fabricated_price(self):
        rows=[stock("005930",price="0",volume="0"),stock("000660")]
        result=p.normalize_market(rows,market="KOSPI",asof=ASOF,source=b"fake",minimum_stocks=2)
        self.assertEqual(result["no_trade_rows_observed"],1)
        self.assertIsNone(result["ohlcv"][0]["ohlc_observed"])
        self.assertEqual(result["ohlcv"][0]["trading_status"],"no_trade_or_suspended")
        self.assertFalse(result["ohlcv"][0]["confirmed_scoring_allowed"])

    def test_reject_underpopulation_or_many_raw_rows(self):
        for rows in ([stock("005930")], [stock(str(x).zfill(6)) for x in range(5001)]):
            with self.subTest(count=len(rows)),self.assertRaises(p.IngestError):
                p.normalize_market(rows,market="KOSPI",asof=ASOF,
                                   source=b"fake",minimum_stocks=2)

    def test_non_stock_identifier_not_treated_as_valid_stock(self):
        rows=[stock("005930"),stock("KR7005930003")]
        with self.assertRaisesRegex(p.IngestError, "not_enough_six_digit_market_codes"):
            p.normalize_market(rows,market="KOSPI",asof=ASOF,
                               source=b"fake",minimum_stocks=2)

    def test_public_repo_private_storage_forbidden(self):
        with self.assertRaisesRegex(p.IngestError, "outside_public_repository"):
            p._private_dir(p.ROOT / "docs" / "raw_krx")

    def test_private_snapshot_symlink_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            target=folder / "target"
            target.write_text("DONOTTOUCH")
            (folder / "krx_20261016.json").symlink_to(target)
            with self.assertRaisesRegex(p.IngestError, "private_snapshot_symlink_forbidden"):
                p._atomically_write_new(folder,"krx_20261016.json",{"asof":ASOF})
            self.assertEqual(target.read_text(), "DONOTTOUCH")

    def test_secret_and_upstream_exception_hidden_from_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=io.StringIO()
            def bad_factory(key):
                raise RuntimeError("HTTP raw AUTH_KEY=SUPPOSED_PRIVATE_KEY")
            with self.assertRaisesRegex(p.IngestError, "unexpected_provider_failure"):
                p.ingest(asof=ASOF,key="SYNTHETIC_PRIVATE_API_KEY",
                         private_dir=Path(tmp),client_factory=bad_factory,
                         minimum_stocks=1)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
