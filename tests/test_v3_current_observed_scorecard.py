"""Strict full-listed same-day scorecard regression; no new provider calls."""
import copy
import csv
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from test_v3_dart_rolling_book_reference import fixture
from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build
from v3_current_observed_scorecard import (
    generate,to_csv,main,LivePartialScorecardError,
)

DOCS=Path(__file__).resolve().parents[1]/"docs"

def small():
    market,catalog=fixture()
    for stock in market["stocks"]:
        entry=next(r for r in catalog["rows"] if r["code"]==stock["code"])
        entry["name"]=stock["name"]
    market["stocks"][0].update({
        "volume_ratio":1.5,"obv_rising":True,
        "price60":2.0,"divergence":True,
    })
    return market,catalog


class CurrentAllListedScorecardTests(unittest.TestCase):
    def test_only_true_observations_are_scored_and_missing_is_not_zero(self):
        market,catalog=small()
        volume=volume_build(market,catalog)
        price=price_build(market,catalog)
        rows,summary=generate(market,catalog,volume,price)
        by={r["code"]:r for r in rows}
        self.assertEqual(summary["listed_total"],5)
        self.assertEqual(summary["provider_requests"],0)
        self.assertEqual(summary["observed_issuer_counts"]["volume"],1)
        self.assertEqual(summary["certified_100point_count"],0)
        self.assertEqual(summary["highest_currently_observable_weight"],49)
        self.assertEqual(by["005930"]["factors"]["volume"],16.0)
        self.assertEqual(by["005930"]["factor_available"]["volume"],16)
        self.assertEqual(by["300004"]["earned"],None)
        self.assertEqual(by["300004"]["available"],0)
        self.assertEqual(by["300004"]["unavailable"],100)
        self.assertEqual(by["005930"]["unavailable"],84)
        txt=to_csv(rows,summary)
        self.assertTrue(txt.startswith("\ufeff"))
        csv_rows=list(csv.DictReader(StringIO(txt.lstrip("\ufeff"))))
        self.assertEqual(len(csv_rows),5)
        self.assertEqual(next(r for r in csv_rows if r["종목코드"]=="300004")
                         ["관측잠정합계_100점중"],"")
        self.assertEqual(next(r for r in csv_rows if r["종목코드"]=="300004")
                         ["미확보배점_100점중"],"100")
        self.assertTrue(all(r["정식투자점수"]=="" for r in csv_rows))

    def test_date_tamper_or_amount_inflation_is_not_accepted(self):
        market,catalog=small()
        v=volume_build(market,catalog)
        p=price_build(market,catalog)
        bad=copy.deepcopy(v)
        bad["records"]["005930"]["observed_points"]=20
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,bad,p)
        bad=copy.deepcopy(p)
        bad["asof"]="2026-10-21"
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,v,bad)
        bad=copy.deepcopy(p)
        bad["certified_100point_investment_issuers"]=3
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,v,bad)

    def test_cli_write_then_verify_then_tamper_csv_rejected(self):
        market,catalog=small()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            sources={
                "data.json":market,
                "v22-market-catalog.json":catalog,
                "v3-volume-obv-observed.json":volume_build(market,catalog),
                "v3-current-price-technical-momentum.json":price_build(market,catalog),
            }
            for name,value in sources.items():
                (root/name).write_text(json.dumps(value),encoding="utf8")
            main(["--docs",str(root),"--mode","write"])
            main(["--docs",str(root),"--mode","verify"])
            p=root/"v3-current-observed-all-listed.csv"
            clean=p.read_text(encoding="utf8")
            p.write_text(clean.replace("16.0","20.0"),encoding="utf8")
            with self.assertRaises(LivePartialScorecardError):
                main(["--docs",str(root),"--mode","verify"])

    def test_current_coverage_publisher_and_homepage_link(self):
        base=Path(__file__).resolve().parents[1]
        flow=(base/".github/workflows/v3-current-observed-scorecard.yml").read_text(encoding="utf8")
        self.assertIn("v3_current_observed_scorecard.py --mode write",flow)
        self.assertIn("v3_current_observed_scorecard.py --mode verify",flow)
        self.assertIn("group: korea-stock-screener-current-observed-publisher",flow)
        self.assertNotIn("DART_API_KEY:",flow)
        self.assertNotIn("KRX_API_KEY:",flow)
        self.assertNotIn("git push --force",flow)
        add=[line for line in flow.splitlines() if line.strip().startswith("git add docs/")]
        self.assertGreaterEqual(len(add),2)
        self.assertTrue(all(
            line.strip()=="git add docs/v3-current-observed-all-listed.csv docs/v3-current-observed-all-listed-summary.json"
            for line in add))
        for html in ("index.html","workspace.html"):
            text=(base/"docs"/html).read_text(encoding="utf8")
            self.assertIn('href="v3-current-observed-all-listed.csv"',text)
            self.assertNotIn('href="v3-all-listed-scores.csv"',text)

    def test_real_saved_market_reconciles_without_rescanning_prices(self):
        docs=DOCS
        required=("data.json","v22-market-catalog.json",
                  "v3-volume-obv-observed.json",
                  "v3-current-price-technical-momentum.json")
        sources=[json.loads((docs/name).read_text(encoding="utf8"))
                 for name in required]
        rows,report=generate(*sources)
        self.assertEqual(len(rows),report["listed_total"])
        self.assertEqual(report["listed_total"],sources[1]["listed_total"])
        self.assertEqual(report["asof"],sources[0]["date"])
        self.assertEqual(report["observed_issuer_counts"]["volume"],
                         sources[2]["observed_volume_candidate_issuers"])
        self.assertEqual(report["observed_issuer_counts"]["technical"],
                         sources[3]["technical_research_issuer_count"])
        self.assertEqual(report["observed_issuer_counts"]["momentum"],
                         sources[3]["momentum_research_issuer_count"])
        self.assertEqual(report["certified_100point_count"],0)
        self.assertEqual(report["observed_issuer_counts"]["financial"],0)
        self.assertTrue(all(r["available"]<=49 for r in rows))


if __name__=="__main__":
    unittest.main()
