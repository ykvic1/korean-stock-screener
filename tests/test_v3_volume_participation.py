"""Offline price-volume factor tests: zero investor-flow assumptions."""
import copy
import csv
from io import StringIO
import json
import tempfile
from pathlib import Path
import unittest
from datetime import date,timedelta

from test_v3_dart_rolling_book_reference import fixture,DAY
from v3_volume_participation import (
    observed,build,to_csv,main,VolumeEvidenceError,SUBMAX,
    market_cap_bias_diagnostic,
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
        self.assertEqual(measured["observed_points"],10)
        self.assertEqual(measured["observed_available_max"],10)
        self.assertEqual(measured["unobserved_max"],10)
        self.assertEqual(sum(SUBMAX.values()),20)
        self.assertIsNone(measured["subpoints"]["post_spike_absorption"])
        self.assertFalse(measured["foreign_institutional_net_buy_inferred"])
        self.assertFalse(measured["investable_score_certified"])
        self.assertFalse(measured["trading_signal_authorized"])

    def test_down_volume_not_assumed_bullish_and_zero_is_not_missing(self):
        bad=observed(stock(price60=-30,ratio=.7,obv=False),
                     asof=DAY,market="KOSPI")
        self.assertEqual(bad["observed_points"],0)
        self.assertEqual(bad["observed_available_max"],10)
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

    def test_ma_cross_replaces_standalone_obv_six_points(self):
        from test_v3_ma240_cross_research import sample
        qualified=sample([100.0]*220+[98.0]*20+[130.0]*20)
        not_qualified=sample([100.0]*260)
        # Proof of dated MA receipt grants exactly six while OBV rise alone cannot.
        first=stock()
        base=observed(first,asof=DAY,market="KOSPI")
        self.assertEqual(base["observed_points"],10)
        self.assertIsNone(base["subpoints"]["ma20_ma60_cross_near_ma240"])
        self.assertNotIn("obv_60_direction",base["subpoints"])
        self.assertEqual(base["unobserved_max"],10)
        # The deterministic historical receipt is rebased to the fixture asof.
        ok=copy.deepcopy(qualified); ok["asof"]=DAY
        first["v3_ma240_cross_research"]=ok
        crossed=observed(first,asof=DAY,market="KOSPI")
        self.assertEqual(crossed["observed_points"],16)
        self.assertEqual(crossed["observed_available_max"],16)
        self.assertEqual(crossed["subpoints"]["ma20_ma60_cross_near_ma240"],6)
        zero=copy.deepcopy(not_qualified); zero["asof"]=DAY
        first["v3_ma240_cross_research"]=zero
        measured=observed(first,asof=DAY,market="KOSPI")
        self.assertEqual(measured["observed_points"],10)
        self.assertEqual(measured["observed_available_max"],16)
        self.assertEqual(measured["subpoints"]["ma20_ma60_cross_near_ma240"],0)
        first["v3_ma240_cross_research"]["source_regular_session_verified"]=True
        invalid=observed(first,asof=DAY,market="KOSPI")
        self.assertEqual(invalid["observed_available_max"],10)
        self.assertIsNone(invalid["subpoints"]["ma20_ma60_cross_near_ma240"])

    def test_market_cap_bias_is_descriptive_and_unknown_score_is_not_zero(self):
        members={
            "000001":{"reference_market_cap_eok":120},
            "000002":{"reference_market_cap_eok":1200},
            "000003":{"reference_market_cap_eok":20000},
            "000004":{"reference_market_cap_eok":60000},
            "000005":{"reference_market_cap_eok":None},
        }
        def measured(points, ceiling, cross):
            return {
                "observed_points":points,
                "observed_available_max":ceiling,
                "subpoints":{"ma20_ma60_cross_near_ma240":cross},
            }
        results={
            "000001":measured(6,16,6),
            "000002":measured(0,10,None),
            "000003":measured(4,16,0),
            "000005":measured(2,10,None),
        }
        before=copy.deepcopy(results)
        audit=market_cap_bias_diagnostic(results,members)
        self.assertEqual(results,before)
        self.assertFalse(audit["investment_points_modified"])
        self.assertFalse(audit["historical_size_bias_proved"])
        self.assertTrue(audit["unknown_volume_scores_never_treated_as_zero"])
        by_bucket={row["bucket"]:row for row in audit["buckets"]}
        micro=by_bucket["under_500_eok"]
        self.assertEqual(micro["mean_observed_points_not_full20"],6)
        self.assertEqual(micro["mean_pct_of_observed_available_ceiling"],37.5)
        self.assertEqual(micro["ma_cross_qualified_pct_of_eligible"],100)
        small=by_bucket["500_to_4999_eok"]
        self.assertEqual(small["mean_observed_points_not_full20"],0)
        self.assertIsNone(small["ma_cross_qualified_pct_of_eligible"])
        mid=by_bucket["5000_to_49999_eok"]
        self.assertEqual(mid["ma_cross_qualified_pct_of_eligible"],0)
        self.assertEqual(mid["mean_pct_of_observed_available_ceiling"],25)
        large=by_bucket["50000_eok_and_above"]
        self.assertEqual(large["score_missing_issuers"],1)
        self.assertIsNone(large["mean_observed_points_not_full20"])
        unknown=by_bucket["market_cap_unavailable"]
        self.assertEqual(unknown["mean_pct_of_observed_available_ceiling"],20)
        self.assertEqual(sum(r["listed_issuers"] for r in audit["buckets"]),5)
        self.assertEqual(sum(r["score_observed_issuers"] for r in audit["buckets"]),4)

    def test_cli_write_then_verify_readback_from_docs_without_double_prefix(self):
        market,catalog=fixture()
        for row in catalog["rows"]:
            for stock in market["stocks"]:
                if row["code"]==stock["code"]:
                    row["name"]=stock["name"]
        market["stocks"][0].update({
            "volume_ratio":1.5,"obv_rising":True,
            "price60":3.0,"divergence":True,
        })
        with tempfile.TemporaryDirectory() as place:
            root=Path(place)
            (root/"data.json").write_text(json.dumps(market),encoding="utf8")
            (root/"v22-market-catalog.json").write_text(
                json.dumps(catalog),encoding="utf8")
            main(["--docs",str(root),"--mode","write"])
            path=root/"v3-volume-obv-observed.json"
            self.assertTrue(path.is_file())
            csv_file=root/"v3-volume-obv-observed.csv"
            self.assertTrue(csv_file.is_file())
            main(["--docs",str(root),"--mode","verify"])
            self.assertFalse((root/"docs").exists())
            csv_text=csv_file.read_text(encoding="utf8")
            self.assertTrue(csv_text.startswith("\ufeff"))
            rows=list(csv.DictReader(StringIO(csv_text.lstrip("\ufeff"))))
            self.assertEqual(len(rows),catalog["listed_total"])
            by_code={r["종목코드"]:r for r in rows}
            self.assertEqual(by_code[market["stocks"][0]["code"]]
                             ["거래량_OBV_잠정점수_20점중"],"10")
            self.assertEqual(by_code[market["stocks"][1]["code"]]
                             ["거래량_OBV_잠정점수_20점중"],"")
            self.assertEqual(by_code[market["stocks"][1]["code"]]
                             ["미확보배점_20점중"],"20")
            self.assertEqual(by_code[market["stocks"][0]["code"]]
                             ["거래급증후매물소화_점수_4점"],"")
            csv_file.write_text(csv_text.replace("10점관측·10점미확보",
                                                  "거짓점수"),encoding="utf8")
            with self.assertRaises(VolumeEvidenceError):
                main(["--docs",str(root),"--mode","verify"])
            csv_file.write_text(csv_text,encoding="utf8")
            altered=json.loads(path.read_text(encoding="utf8"))
            altered["observed_volume_candidate_issuers"]+=1
            path.write_text(json.dumps(altered),encoding="utf8")
            with self.assertRaises(VolumeEvidenceError):
                main(["--docs",str(root),"--mode","verify"])

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
        self.assertEqual(out["records"][s["code"]]["observed_points"],10)
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
