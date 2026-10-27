"""Offline-only checks for complete 100-stock workflow fanout + fail-closed merge."""
from __future__ import annotations

import copy
from datetime import datetime,date,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import full_market_batch as b
import screener
from v3_unscored_path_observations import observe

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

    def test_derived_path_receipt_is_source_guarded_but_never_scored(self):
        last=date.fromisoformat(DAY)
        sessions=[last-timedelta(days=120-i) for i in range(121)]
        evidence=observe([100+i for i in range(121)],[100]*121,
                         asof=DAY,session_days=sessions)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["new_v3_available_points"],0)
        def analyzer(code,cfg,day):
            return {**mock_history(code,cfg,day),
                    "v3_unscored_path_observations":evidence}
        with tempfile.TemporaryDirectory() as tmp:
            m,_,_=self._plan(tmp,105)
            folder,_=self._scan_all(tmp,m,analyzer=analyzer)
            receipts=b.load_receipts(m,folder)
            self.assertEqual(len(receipts["rows"]),105)
            tampered=json.loads((folder/"part-000.json").read_text(encoding="utf8"))
            tampered["rows"][0]["metrics"]["v3_unscored_path_observations"][
                "new_v3_available_points"]=4
            with self.assertRaisesRegex(b.BatchError,
                                        "untrusted_unscored_path_observation"):
                b.validate_part(m,tampered)

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

    def test_missing_one_whole_job_keeps_others_and_reports_true_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _, listed = self._plan(tmp, 255)
            folder, _ = self._scan_all(tmp, manifest)
            (folder/"part-001.json").unlink()
            with self.assertRaises(b.BatchError):
                b.load_receipts(manifest, folder)
            partial = b.load_receipts(manifest, folder, allow_missing_parts=True)
            self.assertEqual(partial["eligible"], 255)
            self.assertEqual(partial["examined"], 155)
            self.assertEqual(partial["deferred"], 100)
            self.assertEqual(partial["missing_batch_parts"], [1])
            self.assertEqual(len(partial["rows"]), 155)
            self.assertFalse(partial["market_scan_complete"])
            self.assertEqual(
                partial["examined"]+partial["deferred"],partial["eligible"])

            def score_stub(row,cfg):
                row.update({"components": {"일봉 구조":5, "주봉·추세":5,
                                           "거래량·OBV":5},
                            "score":30, "coverage_pct":25,
                            "decision":"추가 검증 필요",
                            "risk_flags":["수급·재무 미확인"]})
                return row

            with patch.object(screener, "score_one", side_effect=score_stub):
                output=b.publish(
                    manifest=manifest, parts_dir=folder, listing=listed,
                    optional_sources=[], index_data={}, use_flow=False,
                    write_report=False, allow_missing_parts=True)
            self.assertEqual(len(output["rows"]),155)
            self.assertEqual(output["metadata"]["history_deferred"],100)
            self.assertFalse(output["metadata"]["history_scan_complete"])
            self.assertEqual(output["metadata"]["examined"],155)
            self.assertEqual(output["metadata"]["eligible"],255)

    def test_all_part_artifacts_missing_still_blocks_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _, listed = self._plan(tmp, 105)
            folder=Path(tmp)/"empty"
            folder.mkdir()
            with self.assertRaisesRegex(b.BatchError,"no_successful_batch_artifacts"):
                b.load_receipts(manifest, folder, allow_missing_parts=True)
            with self.assertRaises(b.BatchError):
                b.publish(manifest=manifest, parts_dir=folder, listing=listed,
                          optional_sources=[], index_data={}, use_flow=False,
                          write_report=False, allow_missing_parts=True)

    def test_single_failing_symbol_does_not_cancel_others(self):
        def fails_one(code,cfg,day):
            if code=="100012":
                raise ConnectionError("no local OHLCV")
            return mock_history(code,cfg,day)
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _, _ = self._plan(tmp, 105)
            folder, parts=self._scan_all(tmp,manifest,analyzer=fails_one)
            self.assertEqual(sum(x["attempted"] for x in parts),105)
            self.assertEqual(sum(x["errors"] for x in parts),1)
            full=b.load_receipts(manifest,folder)
            self.assertEqual(len(full["rows"]),104)
            self.assertEqual(len(full["errors"]),1)
            self.assertEqual(full["examined"],105)
            self.assertTrue(full["market_scan_complete"])

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

    def test_scheduled_merger_produces_v3_daily_weekly_technical25_not_faked_100(self):
        """The actual 18:30 route is batch.scan -> batch.publish, not build_rows."""
        def observation(kind):
            study = {
                "schema": "v3-pattern-first-daily-research-0.1",
                "asof": DAY, "timeframe": kind, "status": "heuristic_unverified",
                "patterns": [{"type": "bull_flag", "stage": "breakout_observed"}],
                "rsi14": 41.0, "rsi30_cross_recent": True,
                "rsi_bullish_divergence_recent": False,
                "pattern_geometry": {
                    "status": "heuristic_unvalidated",
                    "falling_wedge_candidate": False,
                    "inverse_head_shoulders_breakout_candidate": False,
                    "triangle_candidate": False,
                    "post_rise_consolidation_candidate": False,
                },
                "scoring_allowed": False, "buy_signal": False,
                "official_regular_session_verified": False,
            }
            if kind == "weekly":
                study["completed_week_only"] = True
                study["weekly_ma120_gap_pct"] = 4.0
            return study

        def research_analyzer(code, cfg, day):
            base = mock_history(code, cfg, day)
            base.update({
                "stop_line": 920.0, "stop_pct": -8.0,
                "one_day_volume_ratio": 1.7,
                "pattern_research": observation("daily"),
                "pattern_weekly_research": observation("weekly"),
            })
            if code == "100001":
                # Missing finished weekly evidence must never become 0 points
                # or be promoted into a full six-pillar investment score.
                base["pattern_weekly_research"] = None
            return base

        def v2_score_stub(row, _cfg):
            row.update({
                "components": {"일봉 구조": 5, "주봉·추세": 5,
                               "거래량·OBV": 5},
                "score": 60, "coverage_pct": 30,
                "decision": "추가 검증 필요",
                "risk_flags": ["수급·재무 미확인"],
            })
            return row

        with tempfile.TemporaryDirectory() as tmp:
            manifest, _, listing = self._plan(tmp, 105)
            parts, _ = self._scan_all(tmp, manifest, analyzer=research_analyzer)
            with patch.object(screener, "score_one", side_effect=v2_score_stub):
                result = b.publish(
                    manifest=manifest, parts_dir=parts, listing=listing,
                    optional_sources=[], index_data={}, use_flow=False,
                    write_report=False)
            rows = result["rows"]
            self.assertEqual(len(rows), 105)
            self.assertEqual(sum(
                x["v3_pattern_integrated"]["technical_research_candidate"] is not None
                for x in rows), 104)
            self.assertEqual(sum(
                x["v3_pattern_integrated"]["technical_research_candidate"] is None
                for x in rows), 1)
            self.assertEqual(next(
                x for x in rows if x["code"] == "100001"
            )["v3_pattern_integrated"]["technical_observed_maximum"], 11)
            complete = next(x for x in rows if x["code"] == "100002")
            item = complete["v3_pattern_integrated"]
            self.assertEqual(item["technical_parts"]["daily_pattern"], 7.0)
            self.assertEqual(item["technical_parts"]["weekly_pattern"], 8.0)
            self.assertEqual(item["technical_research_candidate"], 25.0)
            self.assertEqual(item["six_pillar_coverage_pct"], 25.0)
            self.assertEqual(set(item["missing_pillars"]),
                             {"ias", "financial", "valuation", "industry", "momentum"})
            self.assertIsNone(item["six_pillar_full_candidate_only"])
            self.assertIsNone(item["confirmed_rank"])
            self.assertFalse(item["buy_certified"])
            self.assertEqual(complete["decision"], "추가 검증 필요")
            self.assertEqual(complete["score"], 60)
            self.assertEqual([r["code"] for r in rows], sorted(r["code"] for r in rows))

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
