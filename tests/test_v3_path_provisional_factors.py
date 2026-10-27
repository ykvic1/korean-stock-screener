"""Provisional-only 4+7 path overlay: exact source-date and no false 100 certification."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from test_v3_dart_rolling_book_reference import fixture
from v3_unscored_path_observations import observe, SCHEMA as PATH_SCHEMA
from v3_path_provisional_factors import preview, ProvisionalPathFactorError
from v3_current_observed_scorecard import generate, LivePartialScorecardError
from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build

DAY = "2026-10-19"


def path(*, reversal=False):
    last = date.fromisoformat(DAY)
    sessions = [last-timedelta(days=120-i) for i in range(121)]
    if reversal:
        closes = [100*(0.99**i) for i in range(61)]
        closes += [closes[-1]*(1.01**i) for i in range(1,61)]
    else:
        closes = [100*(1.003**i) for i in range(121)]
    volumes = [100.0]*121
    if not reversal:
        volumes[105] = 300.0
    out = observe(closes, volumes, asof=DAY, session_days=sessions)
    assert out is not None
    return out


def market_with_path():
    daily,catalog = fixture()
    for stock in daily["stocks"]:
        member = next(c for c in catalog["rows"] if c["code"] == stock["code"])
        member["name"] = stock["name"]
    daily["stocks"][0].update({
        "volume_ratio":1.5,"obv_rising":True,"price60":2.0,
        "divergence":True,
        "v3_unscored_path_observations":path(),
    })
    daily["stocks"][1]["v3_unscored_path_observations"] = None
    daily["v3_unscored_path_source"] = {
        "schema":PATH_SCHEMA,
        "observed_issuer_count":1,
        "unavailable_issuer_count":1,
        "additional_volume_or_momentum_points_authorized":0,
        "raw_ohlcv_exported":False,
        "source_exchange_and_adjustments_independently_verified":False,
    }
    return daily,catalog


class ProvisionalPathResearchTests(unittest.TestCase):
    def test_observed_four_and_seven_are_explicitly_research_not_certified(self):
        obs=path(reversal=True)
        a=preview({"price_date":DAY,"v3_unscored_path_observations":obs},asof=DAY)
        self.assertEqual(a["research_available_volume_weight"],4)
        self.assertEqual(a["research_available_momentum_weight"],7)
        self.assertEqual(a["research_volume_post_spike_absorption_points"],0)
        self.assertEqual(a["research_momentum_additional_points"],7)
        self.assertEqual(a["research_momentum_two_segment_120day_rebound_points"],2)
        self.assertIsNone(a["investable_100_point_score"])
        self.assertFalse(a["source_independently_krx_adjusted_verified"])
        self.assertEqual(a["official_additional_investment_points_authorized"],0)
        self.assertFalse(a["historical_hindsight_used"])

    def test_provisional_volume_4_joins_only_same_date_existing_16(self):
        daily,catalog=market_with_path()
        volume=volume_build(daily,catalog)
        price=price_build(daily,catalog)
        rows,summary=generate(daily,catalog,volume,price)
        target=next(x for x in rows if x["code"]=="005930")
        self.assertEqual(target["factor_available"]["volume"],20)
        self.assertEqual(target["factors"]["volume"],20.0)
        self.assertEqual(summary["path_observed_121session_issuers"],1)
        self.assertEqual(summary["path_research_subweight_applied_issuer_counts"],
                         {"volume4":1,"momentum7":0})
        self.assertEqual(summary["factor_observed_weight_ceiling"]["volume"],20)
        self.assertEqual(summary["certified_100point_count"],0)
        self.assertEqual(summary["additional_path_investment_grade_points_authorized"],0)
        self.assertFalse(summary["independent_krx_adjusted_path_certification"])
        self.assertTrue(target["path_provisional_research"]["research_volume_weight_applied"])
        self.assertFalse(target["path_provisional_research"]["research_momentum_weight_applied"])

    def test_full_eight_point_existing_momentum_can_gain_its_observed_seven(self):
        daily,catalog=market_with_path()
        # Synthetic peer-qualified CURRENT-day reference. No future source,
        # provider requests, or claim that actual 80 peers have been fetched.
        mom={
            "schema":"v3-observed-market-momentum-8of10-research-0.1",
            "asof":DAY,"market":"KOSPI",
            "momentum_parts":{
                "relative20":3.0,"relative60":3.0,
                "positive_days20":None,"trend_persistence60":None,
                "reversal_breadth120":None},
            "momentum_observed_maximum":8,
            "momentum_research_candidate":6.0,"momentum_maximum":15,
            "peer20_count":80,"peer60_count":80,
            "market_benchmark_date_independently_verified":False,
            "source_unadjusted_corporate_events_verified":False,
            "positive_days20_observed":False,
            "investable_score":None,"buy_certified":False,
        }
        with patch("v3_current_price_replay.market_momentum",
                   return_value={"005930":mom}):
            v=volume_build(daily,catalog)
            p=price_build(daily,catalog)
            rows,summary=generate(daily,catalog,v,p)
        issuer=next(row for row in rows if row["code"]=="005930")
        self.assertEqual(issuer["factor_available"]["momentum"],15)
        self.assertEqual(issuer["factor_available"]["volume"],20)
        self.assertEqual(issuer["factors"]["momentum"],11.0)
        self.assertEqual(summary["factor_observed_weight_ceiling"]["momentum"],15)
        self.assertEqual(summary["path_research_subweight_applied_issuer_counts"],
                         {"volume4":1,"momentum7":1})
        self.assertEqual(summary["additional_path_investment_grade_points_authorized"],0)
        self.assertEqual(summary["certified_100point_count"],0)

    def test_old_price_snapshots_keep_missing_weight(self):
        daily,catalog=market_with_path()
        daily.pop("v3_unscored_path_source")
        for stock in daily["stocks"]:
            stock.pop("v3_unscored_path_observations",None)
        rows,summary=generate(daily,catalog,volume_build(daily,catalog),
                              price_build(daily,catalog))
        target=next(x for x in rows if x["code"]=="005930")
        self.assertEqual(target["factor_available"]["volume"],16)
        self.assertEqual(summary["path_observed_121session_issuers"],0)

    def test_tampered_new_data_or_forged_authorization_fails_closed(self):
        daily,catalog=market_with_path()
        bad=copy.deepcopy(daily)
        bad["stocks"][0]["v3_unscored_path_observations"]["new_v3_available_points"]=11
        with self.assertRaises(ProvisionalPathFactorError):
            preview(bad["stocks"][0],asof=DAY)
        with self.assertRaises(ValueError):
            generate(bad,catalog,volume_build(bad,catalog),price_build(bad,catalog))
        bad=copy.deepcopy(daily)
        bad["v3_unscored_path_source"]["additional_volume_or_momentum_points_authorized"]=11
        with self.assertRaises(ValueError):
            generate(bad,catalog,volume_build(bad,catalog),price_build(bad,catalog))
        bad=copy.deepcopy(daily)
        bad["stocks"][0]["price_date"]="2026-10-16"
        with self.assertRaises(ProvisionalPathFactorError):
            preview(bad["stocks"][0],asof=DAY)


if __name__=="__main__":
    unittest.main()
