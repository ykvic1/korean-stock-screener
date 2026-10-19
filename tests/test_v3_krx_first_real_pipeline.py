"""Step 12 tests: synthetic KRX response proves end-to-end data path.

Live GitHub Actions mode uses actual KRX API and different output.
Synthetic CI never calls external services or prints confidential rows.
"""
import json
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from v3_krx_first_real_pipeline import (
    run_five_day_evidence, LiveEvidenceError, main,
    FIVE_SESSIONS, PRIVATE_TARGETS,
)


class FakeKRX:
    def __init__(self, *, fail_at=None, tamper=None):
        self.calls = []
        self.fail_at = fail_at
        self.tamper = tamper

    def daily_market(self, market, day):
        self.calls.append((market, day))
        if self.fail_at == (market, day):
            raise RuntimeError("SYNTHETIC_SECRET_TOKEN=DoNotLog")
        price = 100 + len(self.calls)
        stock = "005930" if market == "KOSPI" else "247540"
        rows = [{
            "BAS_DD": day.replace("-", ""), "ISU_CD": stock,
            "TDD_OPNPRC": str(price),
            "TDD_HGPRC": str(price + 5),
            "TDD_LWPRC": str(price - 4),
            "TDD_CLSPRC": str(price + 1),
            "ACC_TRDVOL": "1000",
        }]
        if self.tamper == (market, day):
            rows[0]["BAS_DD"] = "20230101"
        return rows, json.dumps({"OutBlock_1": rows}).encode()


class BoundedRealDataIntegrationTests(unittest.TestCase):
    def test_dry_run_makes_zero_provider_calls(self):
        fake=FakeKRX()
        r=run_five_day_evidence(live=False,key="FAKE",client=fake)
        self.assertEqual(r["actual_api_calls"],0)
        self.assertEqual(r["observed_paired_days"],0)
        self.assertEqual(fake.calls,[])
        self.assertFalse(r["private_source_retained"])

    def test_real_path_reaches_staged_krx_and_optional_technical(self):
        fake=FakeKRX()
        r=run_five_day_evidence(live=True,key="FAKE",client=fake)
        self.assertEqual(len(fake.calls),10)
        self.assertEqual(fake.calls[0],("KOSPI","2026-10-12"))
        self.assertEqual(fake.calls[-1],("KOSDAQ","2026-10-16"))
        self.assertEqual(r["observed_paired_days"],5)
        self.assertEqual(r["actual_api_calls"],10)
        self.assertEqual(r["real_market_source_row_counts"]["KOSPI"],5)
        self.assertEqual(r["real_market_valid_numeric_counts"]["KOSDAQ"],5)
        self.assertEqual(r["observed_valid_target_price_rows"],10)
        self.assertEqual(r["real_target_daily_ma5_available"],2)
        self.assertEqual(r["real_target_daily_ma20_available"],0)
        self.assertTrue(r["real_data_reached_v3_analysis"])
        self.assertTrue(r["source_json_and_hash_chain_internal_consistent"])
        self.assertFalse(r["private_source_retained"])
        self.assertFalse(r["vendor_rows_published"])
        self.assertFalse(r["v3_release_ready"])
        public=json.dumps(r)
        for forbidden in ("005930","247540","TDD_CLSPRC","1000","FAKE"):
            self.assertNotIn(forbidden,public)
        # No private runtime db/path or vendor payload in report.
        self.assertNotIn("transient.sqlite3",public)
        self.assertNotIn("private_results",public)

    def test_failed_market_does_not_publish_or_continue_calls(self):
        fail=("KOSDAQ","2026-10-13")
        fake=FakeKRX(fail_at=fail)
        with self.assertRaisesRegex(
            LiveEvidenceError,"bounded_real_provider_or_semantic_pipeline_failed"):
            run_five_day_evidence(live=True,key="SYNTHETIC_SECRET_TOKEN",client=fake)
        self.assertEqual(fake.calls[-1],fail)
        self.assertEqual(len(fake.calls),4)

    def test_bad_date_source_blocks_audit(self):
        fake=FakeKRX(tamper=("KOSPI","2026-10-15"))
        with self.assertRaisesRegex(LiveEvidenceError,
                                     "bounded_real_provider_or_semantic_pipeline_failed"):
            run_five_day_evidence(live=True,key="FAKE",client=fake)

    def test_missing_secret_and_cli_safe(self):
        with self.assertRaisesRegex(LiveEvidenceError,"krx_secret_not_available"):
            run_five_day_evidence(live=True,key=None)
        out=StringIO()
        with patch.dict("os.environ",{"KRX_API_KEY":""}):
            with redirect_stdout(out):
                code=main(["--live"])
        self.assertEqual(code,1)
        self.assertIn("krx_secret_not_available",out.getvalue())
        self.assertNotIn("KRX_API_KEY",out.getvalue())
        self.assertNotIn("005930",out.getvalue())

    def test_week_contract_is_exactly_five_dates_two_targets(self):
        self.assertEqual(len(FIVE_SESSIONS),5)
        self.assertEqual(FIVE_SESSIONS[0],"2026-10-12")
        self.assertEqual(FIVE_SESSIONS[-1],"2026-10-16")
        self.assertEqual(len(PRIVATE_TARGETS),2)
        self.assertEqual({x["market"] for x in PRIVATE_TARGETS},
                         {"KOSPI","KOSDAQ"})


if __name__ == "__main__":
    unittest.main()
