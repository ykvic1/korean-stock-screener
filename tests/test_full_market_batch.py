"""Offline-only checks for complete 100-stock workflow fanout + fail-closed merge."""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import full_market_batch as b
import screener

DAY = "2026-10-16"


def universe(size=255):
    result = {}
    for i in range(size):
        code = f"{100000+i:06d}"
        result[code] = {
            "code": code, "name": f"Synthetic Stock {i}", "market": "KOSPI" if i % 2 else "KOSDAQ",
            "price": 1500.0, "snapshot_volume": 0 if i % 3 == 0 else 1,
            "market_cap_eok": [1, 100, 1500, 30000, 250000][i%5],
            "trading_eok": [0, 0.05, 5.0][i%3],
            "sector": None, "pbr": None, "valuation_source": None,
        }
    return result


def mock_history(code, cfg, day):
    return {"price_date": DAY, "price_latest": 1000.0,
            "price20": 2.0, "price60": 4.0}


class FullMarketHundredBatchesTests(unittest.TestCase):
    def _plan(self, tmp, n=255):
        listing = universe(n)
        p = Path(tmp) / "plan"
        result = b.plan(asof=DAY, out=p, listing=listing)
        manifest = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
        return manifest, result, listing

    def _scan_all(self, tmp, manifest, analyzer=mock_history):
        folder = Path(tmp) / "parts"
        results=[]
        for part in range(manifest["batch_count"]):
            results.append(b.scan(
                manifest=manifest, part=part, out=folder,
                analyzer=analyzer, sleep=lambda _: None,
            ))
        return folder, results

    def test_market_cap_and_turnover_do_not_remove_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, out, listing = self._plan(tmp, 205)
            self.assertEqual(out["eligible"], 205)
            self.assertEqual(out["chunk_size"], 100)
            self.assertEqual(out["jobs"], 3)
            self.assertEqual(m["batch_count"], 3)
            self.assertEqual(m["codes"], sorted(listing))
            self.assertEqual(len(b.batch_codes(m, 0)), 100)
            self.assertEqual(len(b.batch_codes(m, 1)), 100)
            self.assertEqual(len(b.batch_codes(m, 2)), 5)
            self.assertTrue(any(r["market_cap_eok"] == 1 for r in listing.values()))
            self.assertTrue(any(r["trading_eok"] == 0 for r in listing.values()))
            self.assertFalse(m["source_raw_rows_saved"])
            self.assertFalse(m["company_size_filter"])
            self.assertNotIn("price", (Path(tmp)/"plan"/"manifest.json").read_text())

    def test_complete_255_market_after_three_independent_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, out, _ = self._plan(tmp)
            directory, parts = self._scan_all(tmp, m)
            self.assertEqual([v["attempted"] for v in parts], [100, 100, 55])
            combined = b.load_receipts(m, directory)
            self.assertEqual(combined["eligible"], 255)
            self.assertEqual(combined["examined"], 255)
            self.assertEqual(len(combined["rows"]), 255)
            self.assertEqual(len(combined["errors"]), 0)
            self.assertEqual(len(combined["excluded"]), 0)
            self.assertTrue(combined["market_scan_complete"])
            self.assertFalse(combined["raw_ohlcv_exported"])
            self.assertEqual(set(r["code"] for r in combined["rows"]), set(m["codes"]))

    def test_failed_history_still_accounted_for_with_no_fabricated_zero(self):
        def source(code, cfg, day):
            if code.endswith("00"):
                raise screener.InsufficientHistory("SECRET_URL_DO_NOT_LOG")
            if code.endswith("01"):
                raise TimeoutError("API_KEY=SECRET_DO_NOT_LOG")
            return mock_history(code, cfg, day)
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _ = self._plan(tmp, 201)
            directory, _ = self._scan_all(tmp, m, analyzer=source)
            data = b.load_receipts(m, directory)
            self.assertEqual(len(data["rows"])+len(data["errors"])+len(data["excluded"]), 201)
            self.assertGreater(len(data["errors"]), 0)
            self.assertGreater(len(data["excluded"]), 0)
            raw = "".join(p.read_text(encoding="utf-8") for p in directory.glob("*.json"))
            self.assertNotIn("SECRET_URL", raw)
            self.assertNotIn("SECRET_DO_NOT_LOG", raw)
            self.assertNotIn("0.0", json.dumps(data["errors"]))

    def test_missing_batch_aborts_without_merging_partial_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _ = self._plan(tmp, 201)
            folder, _ = self._scan_all(tmp, m)
            (folder/"part-001.json").unlink()
            with self.assertRaisesRegex(b.BatchError,"missing_or_extra_job_artifact"):
                b.load_receipts(m, folder)

    def test_wrong_digest_or_stock_from_another_day_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _ = self._plan(tmp, 101)
            folder, _ = self._scan_all(tmp, m)
            f = folder/"part-000.json"
            obj = json.loads(f.read_text(encoding="utf-8"))
            obj["digest"] = "0"*64
            with self.assertRaisesRegex(b.BatchError, "identity_or_coverage"):
                b.validate_part(m, obj)
            obj["digest"] = m["digest"]
            obj["asof"] = "2026-10-15"
            with self.assertRaises(b.BatchError):
                b.validate_part(m, obj)

    def test_duplicate_symbol_or_missing_source_identity_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _ = self._plan(tmp, 105)
            folder, _ = self._scan_all(tmp, m)
            f = folder/"part-000.json"
            obj = json.loads(f.read_text(encoding="utf-8"))
            obj["rows"][1]["code"] = obj["rows"][0]["code"]
            with self.assertRaisesRegex(b.BatchError,"missing_duplicate"):
                b.validate_part(m, obj)
            obj["rows"][1]["code"] = m["codes"][1]
            obj["rows"].pop()
            with self.assertRaisesRegex(b.BatchError,"missing_duplicate"):
                b.validate_part(m, obj)

    def test_duplicate_part_unexpected_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _ = self._plan(tmp, 111)
            folder, _ = self._scan_all(tmp, m)
            records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
            with self.assertRaisesRegex(b.BatchError, "duplicate_job_receipt"):
                b.combine_receipts(m, records+[records[0]])

    def test_100_is_processing_unit_not_universe_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, result, _ = self._plan(tmp, 1105)
            self.assertEqual(result["jobs"], 12)
            self.assertEqual(len(m["codes"]), 1105)
            self.assertEqual(len(b.batch_codes(m, 11)), 5)

    def test_forbid_corrupt_universe_duplicate_or_modified_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _ = self._plan(tmp, 205)
            for altered in ("duplicate", "wrong_checksum", "bad_symbol", "wrong_count", "future_date"):
                bad = copy.deepcopy(m)
                if altered == "duplicate": bad["codes"][-1] = bad["codes"][-2]
                if altered == "wrong_checksum": bad["digest"] = "zero"
                if altered == "bad_symbol": bad["codes"][0] = "abcd12"
                if altered == "wrong_count": bad["batch_count"] = 1
                if altered == "future_date": bad["asof"] = "2099-01-01"
                with self.subTest(altered=altered), self.assertRaises(b.BatchError):
                    b.validate_manifest(bad)

    def test_single_complete_market_final_ranking_reuses_existing_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, listed = self._plan(tmp, 105)
            folder, _ = self._scan_all(tmp, m)
            def score_dummy(r, cfg):
                r.update({
                    "components": {"일봉 구조": 5, "주봉·추세": 5,
                                   "거래량·OBV": 5},
                    "score": 60, "coverage_pct": 30,
                    "decision": "추가 검증 필요",
                    "risk_flags": ["실적·현금흐름 검증 필요"],
                })
            with patch.object(screener,"score_one",side_effect=score_dummy):
                combined = b.publish(
                    manifest=m, parts_dir=folder, listing=listed,
                    optional_sources=[], index_data={}, use_flow=False,
                    write_report=False)
            self.assertEqual(len(combined["rows"]), 105)
            self.assertEqual(combined["metadata"]["eligible"], 105)
            self.assertEqual(combined["metadata"]["examined"], 105)
            self.assertEqual(combined["metadata"]["history_deferred"], 0)
            self.assertTrue(combined["metadata"]["history_scan_complete"])
            self.assertFalse(any(r["flows_confirmed"] for r in combined["rows"]))
            self.assertTrue(all(r["decision"] == "추가 검증 필요" for r in combined["rows"]))

    def test_changed_market_listing_blocks_reusing_mismatched_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, listed = self._plan(tmp, 105)
            folder, _ = self._scan_all(tmp, m)
            listed.pop(m["codes"][0])
            with self.assertRaisesRegex(b.BatchError,"listing_changed_between_jobs"):
                b.publish(manifest=m, parts_dir=folder, listing=listed,
                          optional_sources=[], index_data={}, use_flow=False,
                          write_report=False)


if __name__ == "__main__":
    unittest.main()
