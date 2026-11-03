"""Synthetic-only downside strata and unknown same-bar order regression."""
import copy
from datetime import date,timedelta
import unittest

from test_v3_walkforward_research import example
from v3_walkforward_research import evaluate, _outcome, _stop_distance_band, WalkForwardError


def cohort(distances=(10,), events=("flat",), start_year=2020):
    rows=[]
    for q in range(12):
        signal=date(start_year+q//4,1+3*(q%4),6)
        while signal.weekday()>=5:
            signal+=timedelta(days=1)
        for j in range(4):
            i=q*4+j
            case=example(code=f"{i+1:06d}",signal=signal.isoformat(),count=21)
            case["stop_price"]=100-distances[i%len(distances)]
            event=events[i%len(events)]
            if event=="both":
                case["bars"][0].update(low=case["stop_price"]-1,high=111)
            elif event=="stop":
                case["bars"][0].update(low=case["stop_price"]-1)
            elif event=="target":
                case["bars"][0].update(high=111)
            rows.append(case)
    return rows


class StopRiskValidationTests(unittest.TestCase):
    def test_same_bar_ambiguity_is_not_reported_as_definite_stop_probability(self):
        cases=cohort(events=("both","stop","target","flat"))
        before=copy.deepcopy(cases)
        result=evaluate(cases,split_date="2024-01-01",embargo_days=0)
        train=result["periods"]["1m"]["train"]
        self.assertEqual(train["n"],48)
        self.assertEqual(train["ambiguous_same_bar_count"],12)
        self.assertEqual(train["unambiguous_stop_first_count"],12)
        self.assertEqual(train["historical_stop_first_pct"],50)
        self.assertEqual(train["intrabar_order_stop_first_bounds_pct"],{"lower":25,"upper":50})
        self.assertEqual(train["intrabar_order_target_first_bounds_pct"],{"lower":25,"upper":50})
        self.assertFalse(train["intrabar_bounds_are_confidence_intervals"])
        self.assertIsNone(train["forecast_success_probability"])
        self.assertEqual(cases,before)
        self.assertFalse(result["authorized_for_v3_scoring"])

    def test_sample_floors_are_applied_per_band_not_borrowed_from_total(self):
        result=evaluate(cohort(distances=(7,20)),split_date="2024-01-01")
        period=result["periods"]["1m"]
        self.assertTrue(period["train"]["meets_exploratory_sample_floor"])
        bands=period["stop_distance_research"]["bands"]
        self.assertEqual(bands["6_through_9"]["train"]["n"],24)
        self.assertEqual(bands["15_to_under_25"]["train"]["n"],24)
        for group in bands.values():
            self.assertFalse(group["train"]["meets_exploratory_sample_floor"])
            self.assertIsNone(group["train"]["historical_stop_first_pct"])
            self.assertEqual(group["train"]["intrabar_order_stop_first_bounds_pct"],
                             {"lower":None,"upper":None})
        self.assertEqual(sum(g["train"]["n"] for g in bands.values()),period["train"]["n"])

    def test_distance_band_edges_match_existing_risk_overlay_ranges(self):
        pairs=[(2.99,"under_3"),(3,"3_to_under_6"),(6,"6_through_9"),
               (9,"6_through_9"),(9.01,"over_9_to_under_12"),
               (12,"12_to_under_15"),(15,"15_to_under_25"),(25,"25_or_more")]
        for distance,band in pairs:
            self.assertEqual(_stop_distance_band(distance),band)
        for value in (0,-1,100,True,float("nan")):
            with self.assertRaises(WalkForwardError):
                _stop_distance_band(value)

    def test_moving_overlapping_signal_to_another_band_does_not_admit_it(self):
        first=example(signal="2022-01-03",count=21)
        first["stop_price"]=93
        later=example(signal="2022-01-06",count=21)
        later["stop_price"]=80
        period=evaluate([later,first],split_date="2024-01-01")["periods"]["1m"]
        self.assertEqual(period["train"]["n"],1)
        self.assertEqual(period["train"]["overlapping_same_symbol_suppressed"],1)
        bands=period["stop_distance_research"]["bands"]
        self.assertEqual(bands["6_through_9"]["train"]["n"],1)
        self.assertEqual(bands["15_to_under_25"]["train"]["n"],0)

    def test_gap_risk_is_separate_from_uncertain_intrabar_touch(self):
        case=example(count=21)
        case["bars"][1].update(open=85,low=80,high=120,close=100)
        result=_outcome(case,21,30)
        self.assertTrue(result["stop_open_gap_below_frozen_level"])
        self.assertFalse(result["same_bar_stop_target_ambiguous"])
        self.assertAlmostEqual(result["net_return_pct"],-15.3)

    def test_gap_loss_can_exceed_one_initial_risk_unit(self):
        cases=cohort()
        for case in cases:
            case["bars"][1].update(open=85,low=80,high=120,close=100)
        train=evaluate(cases,split_date="2024-01-01")["periods"]["1m"]["train"]
        self.assertEqual(train["stop_open_gap_below_frozen_level_count"],48)
        self.assertEqual(train["historical_mean_net_initial_risk_units"],-1.53)
        self.assertEqual(train["historical_adverse_p75_initial_risk_units"],1.5)

    def test_train_and_holdout_distance_statistics_never_mix(self):
        cases=cohort(distances=(7,),events=("target",),start_year=2010)
        cases+=cohort(distances=(20,),events=("stop",),start_year=2016)
        profile=evaluate(cases,split_date="2015-01-01",embargo_days=0)["periods"]["1m"]["stop_distance_research"]
        bands=profile["bands"]
        self.assertEqual(bands["6_through_9"]["train"]["n"],48)
        self.assertEqual(bands["6_through_9"]["holdout"]["n"],0)
        self.assertEqual(bands["15_to_under_25"]["holdout"]["n"],48)
        self.assertEqual(bands["15_to_under_25"]["train"]["n"],0)
        self.assertEqual(bands["6_through_9"]["train"]["historical_target_first_pct"],100)
        self.assertEqual(bands["15_to_under_25"]["holdout"]["historical_stop_first_pct"],100)
        self.assertFalse(profile["usable_for_live_risk_points"])

    def test_quality_and_frozen_score_bridges_retain_distance_diagnostics(self):
        from test_v3_walkforward_quality_gate import candidate
        from v3_walkforward_quality_gate import gated_walkforward
        from test_v3_five_area_frozen_backtest import row
        from v3_five_area_frozen_backtest import freeze_observed_row, five_area_scored_walkforward
        payload=candidate()
        day=payload["case"]["signal_date"]
        r=row();r.update(code=payload["case"]["code"],market="KOSPI",asof=day)
        snapshot=freeze_observed_row(r,issuer_id=payload["case"]["issuer_id"],
                                     frozen_at_utc=day+"T07:00:00Z")
        payload["frozen_score_snapshot"]=snapshot
        payload["case"]["evidence"]["signal_snapshot_sha256"]=snapshot["snapshot_sha256"]
        plain=gated_walkforward([payload],split_date="2026-01-01")
        self.assertIn("stop_distance_research",plain["periods"]["1m"]["exploratory_walkforward"])
        frozen=five_area_scored_walkforward([payload],split_date="2026-01-01")
        self.assertFalse(frozen["v3_release_ready"])
        nested=frozen["research_quality_gated_walkforward"]["periods"]["1m"]["exploratory_walkforward"]
        self.assertEqual(nested["stop_distance_research"]["bands"]
                         ["over_9_to_under_12"]["train"]["n"],1)


if __name__=="__main__":
    unittest.main()
