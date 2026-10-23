"""Partial V3 momentum research from observed market-relative 20/60 inputs."""
import unittest

from v3_observed_momentum import market_momentum


class MomentumObservedTests(unittest.TestCase):
    def fixture(self,n=100):
        rows=[]
        for i in range(n):
            rows.append({"code":f"{i:06d}","market":"KOSPI",
                         "price_date":"2026-10-19",
                         "relative20":float(i),"relative60":float(n-i)})
        return {"date":"2026-10-19","stocks":rows,
                "index_available":["KOSPI"]}

    def test_research_only_eight_points_midrank_and_missing_weight(self):
        source=self.fixture()
        candidates=market_momentum(source)
        self.assertEqual(len(candidates),100)
        a=candidates["000000"]
        b=candidates["000099"]
        self.assertEqual(a["momentum_observed_maximum"],8)
        self.assertEqual(a["momentum_maximum"],15)
        self.assertIsNone(a["momentum_parts"]["trend_persistence60"])
        self.assertIsNone(a["momentum_parts"]["reversal_breadth120"])
        self.assertIsNone(a["momentum_parts"]["positive_days20"])
        self.assertFalse(a["market_benchmark_date_independently_verified"])
        self.assertFalse(a["buy_certified"])
        self.assertIsNone(a["investable_score"])
        self.assertAlmostEqual(a["momentum_research_candidate"],4)
        self.assertAlmostEqual(b["momentum_research_candidate"],4)
        self.assertNotEqual(a["momentum_parts"]["relative20"],
                            b["momentum_parts"]["relative20"])

    def test_none_is_missing_not_zero_or_ranked_unverified_cohort(self):
        source=self.fixture()
        source["stocks"][0]["relative20"]=None
        candidates=market_momentum(source)
        a=candidates["000000"]
        self.assertIsNone(a["momentum_parts"]["relative20"])
        self.assertEqual(a["momentum_observed_maximum"],4)
        self.assertGreater(a["momentum_research_candidate"],0)
        source["index_available"]=[]
        self.assertEqual(market_momentum(source),{})

    def test_market_and_source_day_not_mixed(self):
        source=self.fixture(79)
        self.assertTrue(all(x["momentum_research_candidate"] is None
                            for x in market_momentum(source).values()))
        source=self.fixture(100)
        source["stocks"][0]["price_date"]="2026-10-18"
        c=market_momentum(source)
        self.assertNotIn("000000",c)
        source["stocks"][0]["price_date"]="2026-10-19"
        source["stocks"][0]["relative20"]=float("nan")
        a=market_momentum(source)["000000"]
        self.assertIsNone(a["momentum_parts"]["relative20"])


if __name__=="__main__":
    unittest.main()
