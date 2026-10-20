"""Offline safety tests: five-day KRX size sample never publishes price rows."""
import json
import unittest

from v3_krx_five_day_size_probe import DAYS,SizeProbeError,measure


class FakeClient:
    def __init__(self,fail_at=None):
        self.calls=[]
        self.fail_at=fail_at

    def daily_market(self,market,day):
        self.calls.append((market,day))
        if self.fail_at==(market,day):
            raise ValueError("private supplier payload never logged")
        rows=[]
        for k in range(100):
            rows.append({
                "BAS_DD":day.replace("-",""),"ISU_CD":f"{k+(0 if market=='KOSPI' else 30000):06d}",
                "TDD_OPNPRC":str(10000+k),"TDD_HGPRC":str(10100+k),
                "TDD_LWPRC":str(9900+k),"TDD_CLSPRC":str(10020+k),
                "ACC_TRDVOL":str(100000+k*19)})
        return rows,json.dumps({"OutBlock_1":rows}).encode()


class KRXFiveDaySizeTests(unittest.TestCase):
    def test_five_complete_pairs_only_counts_bytes_not_values(self):
        client=FakeClient()
        report=measure(client)
        self.assertEqual(report["sample_trade_days"],5)
        self.assertEqual(report["total_official_market_calls"],10)
        self.assertEqual(len(client.calls),10)
        self.assertEqual(report["source_market_rows"],1000)
        self.assertLess(report["gzip_source_bytes"],report["raw_source_bytes"])
        self.assertFalse(report["raw_response_persisted"])
        self.assertFalse(report["licensed_permanent_retention_verified"])
        self.assertEqual(report["private_archive_files_written"],0)
        self.assertTrue(all(x["both_markets_validated"] for x in report["per_date"]))
        public=json.dumps(report)
        self.assertNotIn("TDD_OPNPRC",public)
        self.assertNotIn("ACC_TRDVOL",public)

    def test_cannot_silently_expand_ten_call_budget(self):
        for dates in (DAYS[:4],DAYS+DAYS[:1],
                      (DAYS[0],)*5,
                      ("2026-10-17",)+DAYS[1:]):
            with self.assertRaises(SizeProbeError):
                measure(FakeClient(),dates)

    def test_failure_never_exposes_provider_payload_in_errors(self):
        fake=FakeClient(fail_at=("KOSDAQ",DAYS[2]))
        with self.assertRaisesRegex(SizeProbeError,
                                    "^official_market_probe_failed$") as caught:
            measure(fake)
        self.assertNotIn("private supplier payload",str(caught.exception))

if __name__=="__main__":
    unittest.main()
