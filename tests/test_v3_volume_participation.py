"""Offline price-volume factor tests: zero investor-flow assumptions."""
import copy
import unittest
from datetime import date,timedelta

from test_v3_dart_rolling_book_reference import fixture,DAY
from v3_volume_participation import (
    observed,build,VolumeEvidenceError,SUBMAX,
)


def stock(price60=2,ratio=1.5,obv=True):
    return {"market":"KOSPI","price_date":DAY,
            "volume_ratio":ratio,"obv_rising":obv,
            "price60":price60,
            "divergence":bool(obv and -12<=price60<=10),
            "one_day_volume_ratio":99, # TECHNICAL ONLY. MUST BE IGNORED.
            "flows_confirmed":False}


class ObservedVolumeTests(unittest.TestCase):
    def test_real_volume_obv_candidates_without_investor_attribution(self):
        measured=observed(stock(),asof=DAY,market="KOSPI")
        self.assertEqual(measured["observed_points"],16)
        self.assertEqual(measured["observed_available_max"],16)
        self.assertEqual(measured["unobserved_max"],4)
        self.assertEqual(sum(SUBMAX.values()),20)
        self.assertIsNone(measured["subpoints"]["post_spike_absorption"])
        self.assertFalse(measured["foreign_institutional_net_buy_inferred"])
        self.assertFalse(measured["investable_score_certified"])
        self.assertFalse(measured["trading_signal_authorized"])

    def test_down_volume_not_assumed_bullish_and_zero_is_not_missing(self):
        bad=observed(stock(price60=-30,ratio=.7,obv=False),
                     asof=DAY,market="KOSPI")
        self.assertEqual(bad["observed_points"],0)
        self.assertEqual(bad["observed_available_max"],16)
        self.assertIn("price_downtrend_requires_support_confirmation",
                      bad["risk_flags"])
        sudden=observed(stock(ratio=8),asof=DAY,market="KOSPI")
        self.assertLess(sudden["observed_points"],
                        observed(stock(ratio=1.5),asof=DAY,market="KOSPI")["observed_points"])
        self.assertIn("excess_20day_volume_not_accumulation",
                      sudden["risk_flags"])

    def test_single_day_volume_and_flow_identity_do_not_change_scores(self):
        a=stock()
        baseline=observed(a,asof=DAY,market="KOSPI")
        b=copy.deepcopy(a)
        b["one_day_volume_ratio"]=0
        b["flows_confirmed"]=True
        b["foreign_shares"]=500000000
        b["inst_shares"]=-100000000
        self.assertEqual(observed(b,asof=DAY,market="KOSPI"),baseline)
        self.assertNotIn("investor_quality",baseline["subpoints"])

    def test_malformed_missing_or_stale_evidence_never_grants_points(self):
        original=stock()
        for broken in (
            dict(original,volume_ratio=None),
            dict(original,obv_rising=None),
            dict(original,divergence=True,obv_rising=False),
            dict(original,divergence=False),
            dict(original,volume_ratio=True),
            dict(original,price60=float("nan")),
            dict(original,volume_ratio=-1),
            dict(original,price_date=(date.fromisoformat(DAY)-timedelta(days=1)).isoformat()),
            dict(original,market="KOSDAQ"),
        ):
            self.assertIsNone(observed(broken,asof=DAY,market="KOSPI"))
        self.assertEqual(
            observed(stock(price60=35),asof=DAY,market="KOSPI")
            ["subpoints"]["obv_price_not_reflected"],0)

    def test_full_listed_catalog_missing_is_not_zero_score(self):
        market,catalog=fixture()
        s=market["stocks"][0]
        s.update({"volume_ratio":1.5,"obv_rising":True,
                  "price60":3.0,"divergence":True,
                  "one_day_volume_ratio":12.5})
        for entry in catalog["rows"]:
            for current in market["stocks"]:
                if entry["code"]==current["code"]:
                    entry["name"]=current["name"]
        for later in market["stocks"][1:]:
            later.update({"volume_ratio":None})
        out=build(market,catalog)
        self.assertEqual(out["listed_total"],catalog["listed_total"])
        self.assertEqual(out["observed_volume_candidate_issuers"],1)
        self.assertEqual(out["insufficient_or_unpriced_issuers"],
                         catalog["listed_total"]-1)
        self.assertEqual(out["certified_100_point_investment_scores"],0)
        self.assertFalse(out["ias_investor_flow_component_used"])
        self.assertTrue(out["technical_one_day_volume_not_double_counted"])
        self.assertEqual(out["records"][s["code"]]["observed_points"],16)
        self.assertNotIn(market["stocks"][1]["code"],out["records"])
        wrong=copy.deepcopy(catalog)
        wrong["date"]=(date.fromisoformat(DAY)-timedelta(days=1)).isoformat()
        with self.assertRaises(VolumeEvidenceError):
            build(market,wrong)
        mismatch=copy.deepcopy(catalog)
        code=s["code"]
        for row in mismatch["rows"]:
            if row["code"]==code:
                row["market"]="KOSDAQ"
        with self.assertRaises(VolumeEvidenceError):
            build(market,mismatch)


if __name__=="__main__":
    unittest.main()
