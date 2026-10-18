"""Synthetic runnable v3 dry-run: 6-factor pipeline, no official API or orders."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from v3_offline_workbench import (
    WorkbenchError, demo_bundle, run_workbench
)


class OfflineSixPillarWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.synthetic = demo_bundle()
        cls.complete = run_workbench(cls.synthetic)

    def test_demo_all_six_pillars_computationally_connected_not_authorized(self):
        r = self.complete
        self.assertEqual(r["research_stock_count"], 64)
        self.assertEqual(r["market_cohort_sizes"], {"KOSPI": 32, "KOSDAQ": 32})
        self.assertEqual(r["fully_covered_six_pillar_research_only"], 64)
        self.assertEqual(r["no_input_evidence_or_partial"], 0)
        self.assertEqual(len(r["stocks_in_code_order_not_ranked"]), 64)
        self.assertTrue(all(
            row["candidate_full_total_research_only"] is not None
            and row["candidate_coverage_pct"] == 100
            for row in r["stocks_in_code_order_not_ranked"]
        ))
        self.assertTrue(all(
            set(row["pillar_research_candidates"]) ==
            {"ias", "financial", "valuation", "technical", "industry", "momentum"}
            for row in r["stocks_in_code_order_not_ranked"]
        ))
        self.assertIsNone(r["confirmed_stock_ranks"])
        self.assertIsNone(r["expected_returns"])
        self.assertIsNone(r["recommended_positions"])
        self.assertFalse(r["confirmed_buy"])
        self.assertFalse(r["live_krx_or_dart_api_called"])
        self.assertFalse(r["v3_release_ready"])

    def test_absent_flow_and_price_never_fabricate_score(self):
        payload = {"asof": self.synthetic["asof"], "stocks": []}
        for s in self.synthetic["stocks"][:32]:
            payload["stocks"].append({
                "asof": s["asof"], "code": s["code"],
                "issuer_id": s["issuer_id"], "market": s["market"],
                "manual_raw_features": s["manual_raw_features"]
            })
        r = run_workbench(payload)
        self.assertEqual(r["fully_covered_six_pillar_research_only"], 0)
        self.assertEqual(r["no_input_evidence_or_partial"], 32)
        self.assertTrue(all(
            row["pillar_research_candidates"]["ias"] is None and
            row["pillar_research_candidates"]["technical"] is None
            for row in r["stocks_in_code_order_not_ranked"]))
        self.assertFalse(r["confirmed_buy"])

    def test_missing_flow_observation_remains_missing_only_for_affected_stock(self):
        sample = copy.deepcopy(self.synthetic)
        sample["stocks"][0]["flow_ledger"]["observations"][-1]["institution_total_net_shares"] = None
        r = run_workbench(sample)
        self.assertEqual(r["fully_covered_six_pillar_research_only"], 63)
        self.assertEqual(r["no_input_evidence_or_partial"], 1)
        affected = [x for x in r["stocks_in_code_order_not_ranked"]
                    if x["code"] == "000001"][0]
        self.assertIsNone(affected["ias_research_components"]["accumulated_net_buy"])
        self.assertIsNone(affected["candidate_full_total_research_only"])
        self.assertIsNone(affected["confirmed_rank"])

    def test_hypothetical_risk_uses_explicit_input_not_auto_position_size(self):
        inputs = {"asof": self.synthetic["asof"],
                  "stocks": self.synthetic["stocks"][:32],
                  "hypothetical_portfolio_plans": [{
                      "code": "000001", "entry_price": 100,
                      "stop_price": 92, "planned_position_pct": 20,
                      "stress_gap_below_stop_pct": 5,
                      "reference_target_price": 120}],
                  "hypothetical_nav_risk_budget_pct": 1}
        r = run_workbench(inputs)
        risk = r["hypothetical_portfolio_risk_only"]
        self.assertEqual(risk["stress_budget_status"], "exceeds_assumed_budget")
        self.assertAlmostEqual(risk["total_hypothetical_gap_loss_of_nav_pct"], 2.52)
        self.assertIsNone(risk["safe_position_size_pct"])
        self.assertFalse(risk["trade_approval"])
        self.assertFalse(r["confirmed_buy"])

    def test_input_manual_cannot_overwrite_ias_or_technical(self):
        original = self.synthetic["stocks"][0]
        for metric in ("ias_flow250_pct_float",
                       "technical_pattern_confirmation_strength",
                       "momentum_stock_relative_strength_60_pct",
                       "industry_relative_strength_60_pct"):
            row = {**original, "manual_raw_features": {metric: 100}}
            with self.subTest(metric=metric), self.assertRaisesRegex(
                    WorkbenchError, "unknown_or_unauthed_manual_metric"):
                run_workbench({"asof": original["asof"], "stocks": [row]})

    def test_cross_issuer_flow_and_stale_price_are_blocked(self):
        original = self.synthetic["stocks"][0]
        s = {**original, "flow_ledger": {**original["flow_ledger"],
                                        "issuer_id": "99999999"}}
        with self.assertRaisesRegex(WorkbenchError, "cross_issuer_investor_flow"):
            run_workbench({"asof": original["asof"], "stocks": [s]})
        s = {**original, "price_bars": original["price_bars"][:-1]}
        with self.assertRaisesRegex(WorkbenchError, "unusable_price_observations"):
            run_workbench({"asof": original["asof"], "stocks": [s]})

    def test_invalid_portfolio_budget_and_unknown_code_fail(self):
        original = self.synthetic["stocks"][0]
        subset = {"asof": original["asof"], "stocks": [original]}
        subset["hypothetical_portfolio_plans"] = [{
            "code": "999999", "entry_price": 100,
            "stop_price": 95, "planned_position_pct": 10,
            "stress_gap_below_stop_pct": 2}]
        subset["hypothetical_nav_risk_budget_pct"] = 1
        with self.assertRaisesRegex(WorkbenchError, "portfolio_unknown_research_symbol"):
            run_workbench(subset)
        subset["hypothetical_portfolio_plans"][0]["code"] = original["code"]
        subset["hypothetical_nav_risk_budget_pct"] = None
        with self.assertRaisesRegex(WorkbenchError, "incomplete_portfolio_risk_inputs"):
            run_workbench(subset)

    def test_cli_demo_is_explicitly_synthetic_and_contains_no_buy(self):
        command = [sys.executable, "-m", "v3_offline_workbench", "--demo"]
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr[-500:])
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["dataset"], "SYNTHETIC_DEMO")
        self.assertEqual(payload["research_stock_count"], 64)
        self.assertIsNone(payload["confirmed_stock_ranks"])
        self.assertFalse(payload["confirmed_buy"])
        self.assertFalse(payload["v3_release_ready"])

    def test_cli_user_file_labeled_unverified_and_no_api_key_leaked(self):
        stock = self.synthetic["stocks"][0]
        sample = {"asof": stock["asof"], "stocks": [
            {"code": stock["code"], "issuer_id": stock["issuer_id"],
             "asof": stock["asof"], "market": stock["market"],
             "manual_raw_features": stock["manual_raw_features"],
             "secret_key_should_not_publish": "SYNTHETIC_SECRET_VALUE"}]}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "private.json"
            target.write_text(json.dumps(sample), encoding="utf-8")
            command = [sys.executable, "-m", "v3_offline_workbench",
                       "--input", str(target)]
            p = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        value = json.loads(p.stdout)
        self.assertEqual(value["dataset"], "UNVERIFIED_LOCAL_INPUT")
        self.assertNotIn("SYNTHETIC_SECRET_VALUE", p.stdout)
        self.assertFalse(value["raw_price_or_investor_flows_publicly_returned"])
        self.assertFalse(value["confirmed_buy"])


if __name__ == "__main__":
    unittest.main()
