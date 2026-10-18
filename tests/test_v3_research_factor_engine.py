"""Executable six-pillar cohort research engine: synthetic inputs, NO market API."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import json
import math
import unittest

from v3_research_factor_engine import (
    FactorEngineError, FIELD_SPECS, IAS_METRICS, PILLAR_METRICS,
    _validate_design, ias_raw_features_from_ledger, research_factor_batch,
)
from v3_score_contract import IAS_POINTS, PILLAR_POINTS


def ledger(count=250):
    d = date(2026, 10, 16)
    rows = []
    while len(rows) < count:
        if d.weekday() < 5:
            rows.append({
                "date": d.isoformat(), "session": "KRX_REGULAR_0900_1530_ASIA_SEOUL",
                "market": "KOSPI",
                "foreign_net_shares": 10,
                "institution_total_net_shares": 20,
                "pension_net_shares": 5,
            })
        d -= timedelta(days=1)
    return {
        "code": "000001", "issuer_id": "00000001", "market": "KOSPI",
        "institution_definition": "total_includes_pension",
        "pension_definition": "pension_etc_subcategory",
        "observations": list(reversed(rows)),
    }


def cohort(n=40, *, market="KOSPI"):
    result = []
    for i in range(n):
        row = {}
        for name, (_, direction, bounds) in FIELD_SPECS.items():
            # All fields well within respective valid ranges, no huge scales.
            if bounds == (0, 1):
                row[name] = (i + 1) / (n + 1)
            elif name == "valuation_pbr":
                row[name] = 0.8 + (i / n)
            elif name == "technical_volume_breakout_ratio":
                row[name] = 0.5 + (i / n)
            else:
                row[name] = 5 + i
        result.append({
            "code": f"{(i + (100000 if market == 'KOSDAQ' else 0)):06d}",
            "issuer_id": f"{i + (10000000 if market == 'KOSDAQ' else 0):08d}",
            "market": market, "asof": "2026-10-16", "features": row
        })
    return result


def run(rows):
    return research_factor_batch(rows, asof="2026-10-16", min_peers=30)


class SixPillarRawFactorTests(unittest.TestCase):
    def test_all_six_explicit_weight_budgets_are_executable(self):
        _validate_design()
        self.assertEqual(sum(PILLAR_POINTS.values()), 100)
        self.assertEqual(sum(IAS_POINTS.values()), 100)
        self.assertEqual(set(PILLAR_METRICS), set(PILLAR_POINTS) - {"ias"})
        self.assertEqual(set(IAS_METRICS), set(IAS_POINTS))
        self.assertEqual(sum(x[1] for x in IAS_METRICS["accumulated_net_buy"]), 25)
        self.assertEqual(len(FIELD_SPECS), len(set(FIELD_SPECS)))

    def test_synthetic_complete_cohort_calculates_all_six_without_confirmed_rank(self):
        result = run(cohort())
        self.assertEqual(result["market_cohorts"], {"KOSPI": 40})
        rows = result["rows"]
        self.assertEqual(len(rows), 40)
        for row in rows:
            self.assertEqual(row["candidate_coverage_pct"], 100)
            self.assertIsNotNone(row["candidate_full_total_research_only"])
            self.assertTrue(0 <= row["candidate_full_total_research_only"] <= 100)
            self.assertEqual(set(row["pillar_research_candidates"]), set(PILLAR_POINTS))
            self.assertTrue(all(v is not None for v in row["ias_research_components"].values()))
            self.assertIsNone(row["confirmed_score"])
            self.assertIsNone(row["confirmed_rank"])
            self.assertIsNone(row["predicted_return_pct"])
            self.assertFalse(row["confirmed_buy"])
        self.assertFalse(result["normalization_calibrated_out_of_sample"])
        self.assertFalse(result["original_flow_price_fundamental_data_verified"])
        self.assertFalse(result["v3_release_ready"])
        self.assertIsNone(result["confirmed_full_market_rank"])

    def test_ias_flow_float_normalization_and_pension_not_counted_twice(self):
        values = ias_raw_features_from_ledger(
            ledger(), asof="2026-10-16", float_shares=100000,
            ownership_delta_250_pp=1.3, obv_change_60_pct=8,
            price_return_60_pct=-2)
        for d in (20, 60, 120, 250):
            self.assertAlmostEqual(values[f"ias_flow{d}_pct_float"],
                                   d * 30 / 100000 * 100)
        self.assertEqual(values["ias_buy_days_60_ratio"], 1)
        self.assertEqual(values["ias_foreign_pension_cobuy_60_ratio"], 1)
        self.assertEqual(values["ias_unreflected_price_60_pct"], 2)
        self.assertEqual(values["ias_ownership_delta_250_pp"], 1.3)

    def test_true_zero_is_not_missing_but_missing_is_not_zero(self):
        flow = ledger(60)
        flow["observations"][-1]["foreign_net_shares"] = 0
        values = ias_raw_features_from_ledger(
            flow, asof="2026-10-16", float_shares=100000)
        self.assertIsNotNone(values["ias_flow20_pct_float"])
        self.assertIsNone(values["ias_flow120_pct_float"])
        self.assertIsNone(values["ias_ownership_delta_250_pp"])
        self.assertIsNone(values["ias_unreflected_price_60_pct"])
        flow["observations"][-1]["institution_total_net_shares"] = None
        values = ias_raw_features_from_ledger(
            flow, asof="2026-10-16", float_shares=100000)
        self.assertIsNone(values["ias_flow20_pct_float"])
        self.assertIsNone(values["ias_buy_days_60_ratio"])
        self.assertEqual(values["ias_foreign_pension_cobuy_60_ratio"], 59/60)

    def test_unreflected_price_needs_positive_flow_and_nonfalling_obv(self):
        flow = ledger(60)
        for r in flow["observations"]:
            r["foreign_net_shares"] = -100
        values = ias_raw_features_from_ledger(
            flow, asof="2026-10-16", float_shares=100000,
            obv_change_60_pct=10, price_return_60_pct=-35)
        self.assertIsNone(values["ias_unreflected_price_60_pct"])
        flow = ledger(60)
        values = ias_raw_features_from_ledger(
            flow, asof="2026-10-16", float_shares=100000,
            obv_change_60_pct=-1, price_return_60_pct=-35)
        self.assertIsNone(values["ias_unreflected_price_60_pct"])

    def test_narrow_cohort_and_missing_metric_create_null_not_invented_full_score(self):
        short = run(cohort(8))
        self.assertEqual(short["rows"][0]["candidate_coverage_pct"], 0)
        self.assertIsNone(short["rows"][0]["candidate_full_total_research_only"])
        self.assertIsNone(short["rows"][0]["candidate_normalized_100_research_only"])
        many = cohort()
        many[0]["features"].pop("financial_fcf_assets_3y_pct")
        report = run(many)
        self.assertIsNone(report["rows"][0]["pillar_research_candidates"]["financial"])
        self.assertIsNone(report["rows"][0]["candidate_full_total_research_only"])
        self.assertGreater(report["rows"][0]["candidate_coverage_pct"], 0)
        self.assertLess(report["rows"][0]["candidate_coverage_pct"], 100)
        self.assertIn("financial_fcf_assets_3y_pct", report["rows"][0]["missing_features"])

    def test_peer_comparison_midrank_tie_does_not_pretend_all_are_best(self):
        same = cohort()
        for r in same:
            r["features"] = {field: 1 for field in r["features"]}
        result = run(same)
        a = result["rows"][0]
        b = result["rows"][-1]
        self.assertEqual(a["candidate_full_total_research_only"], 50)
        self.assertEqual(b["candidate_full_total_research_only"], 50)
        self.assertEqual(a["ias_research_components"]["accumulated_net_buy"], 12.5)
        self.assertIsNone(a["confirmed_rank"])

    def test_opposite_pbr_direction_cannot_rewards_high_multiple(self):
        result = run(cohort())
        a, z = result["rows"][0], result["rows"][-1]
        # Valuation pbr is inverse; earnings and FCF yields trend forward.
        self.assertGreater(z["pillar_research_candidates"]["valuation"],
                           a["pillar_research_candidates"]["valuation"])
        self.assertLess(z["candidate_full_total_research_only"], 100)

    def test_kospi_and_kosdaq_never_share_peer_baselines(self):
        x = cohort(40)
        y = cohort(40, market="KOSDAQ")
        for item in y:
            for name in FIELD_SPECS:
                item["features"][name] += 100 if FIELD_SPECS[name][2] is None else 0
        all_rows = run(x+y)
        self.assertEqual(all_rows["market_cohorts"], {"KOSDAQ": 40, "KOSPI": 40})
        self.assertEqual(
            all_rows["rows"][0]["candidate_full_total_research_only"],
            all_rows["rows"][40]["candidate_full_total_research_only"])
        self.assertTrue(all(v == 40 for v in all_rows["rows"][0]["per_metric_valid_peer_count"].values()))

    def test_invalid_metrics_and_duplicate_or_cross_date_blocked(self):
        cases = {
            "bool": lambda x: x[0]["features"].update(valuation_pbr=True),
            "nan": lambda x: x[0]["features"].update(valuation_pbr=float("nan")),
            "negative_pbr": lambda x: x[0]["features"].update(valuation_pbr=-1),
            "invalid_ratio": lambda x: x[0]["features"].update(ias_buy_days_60_ratio=1.1),
            "bad_code": lambda x: x[0].update(code="0ABC"),
            "bad_issuer": lambda x: x[0].update(issuer_id="abc"),
            "wrong_market": lambda x: x[0].update(market="NYSE"),
            "stale_day": lambda x: x[0].update(asof="2026-10-15"),
            "extra_feature": lambda x: x[0]["features"].update(invented=5),
            "duplicate_symbol": lambda x: x.append(copy.deepcopy(x[0])),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                x = cohort()
                change(x)
                with self.assertRaises(FactorEngineError):
                    run(x)

    def test_ledger_denominator_stale_and_bad_input_rejected(self):
        for denominator in (0, -1, 1.5, True):
            with self.subTest(denominator=denominator), self.assertRaises(FactorEngineError):
                ias_raw_features_from_ledger(ledger(), asof="2026-10-16",
                                             float_shares=denominator)
        with self.assertRaisesRegex(FactorEngineError, "stale"):
            ias_raw_features_from_ledger(ledger(20), asof="2026-10-19",
                                         float_shares=100000)

    def test_do_not_publish_underlying_flow_or_source_keys_in_research_output(self):
        x = cohort()
        x[0]["features"]["ias_ownership_delta_250_pp"] = 12345678
        report = run(x)
        raw = json.dumps(report)
        self.assertNotIn("12345678", raw)
        self.assertNotIn("API_KEY", raw)
        self.assertNotIn("confirmed_buy\": true", raw)


if __name__ == "__main__":
    unittest.main()
