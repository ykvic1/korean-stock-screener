"""No-new-source technical/momentum all-listed replay regression."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import v3_current_price_replay as replay
from test_v3_dart_rolling_book_reference import fixture,DAY

DOCS=Path(__file__).resolve().parents[1]/"docs"


def tiny():
    market,catalog=fixture()
    for stock in market["stocks"]:
        member=next(item for item in catalog["rows"]
                    if item["code"]==stock["code"])
        member["name"]=stock["name"]
    market["index_available"]=["KOSPI","KOSDAQ"]
    return market,catalog


class CurrentPriceReplayTests(unittest.TestCase):
    def test_one_valid_partial_technical_replayed_without_dart_or_ias(self):
        market,catalog=tiny()
        s=market["stocks"][0]
        s["v3_pattern_integrated"]={
            "schema":"v3-pattern-within-six-pillars-research-0.4",
            "asof":DAY,"technical_maximum":25,
            "technical_weights":{
                "daily_pattern":7,"weekly_pattern":8,"daily_rsi":2,
                "weekly_rsi":3,"weekly_ma120":3,"volume_confirmation":2},
            "technical_parts":{
                "daily_pattern":4.0,"weekly_pattern":None,"daily_rsi":1,
                "weekly_rsi":None,"weekly_ma120":None,
                "volume_confirmation":2},
            "technical_observed_maximum":11,
            "technical_research_candidate":None,
            "buy_certified":False,"v3_release_ready":False,
            "investable_score":None}
        result=replay.build(market,catalog)
        self.assertEqual(result["listed_total"],5)
        self.assertEqual(result["priced_current_session"],2)
        self.assertEqual(result["technical_research_issuer_count"],1)
        self.assertEqual(result["momentum_research_issuer_count"],0)
        ref=result["rows"][s["code"]]
        tech=ref["technical_research_reference"]
        self.assertEqual(tech["technical_observed_maximum"],11)
        self.assertEqual(tech["technical_parts"]["daily_pattern"],4)
        self.assertFalse(ref["official_investment_score_certified"])
        self.assertEqual(result["separate_source_provider_calls"],0)
        self.assertFalse(result["official_ias_or_buyer_identity_inferred"])
        self.assertNotIn("volume_raw",json.dumps(result))

    def test_current_saved_market_updates_all_priced_without_new_provider(self):
        market=json.loads((DOCS/"data.json").read_text(encoding="utf8"))
        catalog=json.loads((DOCS/"v22-market-catalog.json").read_text(encoding="utf8"))
        result=replay.build(market,catalog)
        self.assertEqual(result["listed_total"],catalog["listed_total"])
        self.assertEqual(result["asof"],market["date"])
        self.assertEqual(result["priced_current_session"],len(market["stocks"]))
        self.assertLessEqual(result["technical_research_issuer_count"],
                             len(market["stocks"]))
        self.assertLessEqual(result["momentum_research_issuer_count"],
                             len(market["stocks"]))
        self.assertEqual(result["certified_100point_investment_issuers"],0)
        self.assertEqual(result["separate_source_provider_calls"],0)
        for code,record in result["rows"].items():
            self.assertEqual(record["asof"],market["date"])
            self.assertFalse(record["official_investment_score_certified"])
        shifted=copy.deepcopy(catalog)
        shifted["date"]="2026-10-20" if market["date"]!="2026-10-20" else "2026-10-19"
        with self.assertRaises(Exception):
            replay.build(market,shifted)

    def test_cli_write_verify_readback_rejects_tampering(self):
        market,catalog=tiny()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"data.json").write_text(json.dumps(market),encoding="utf8")
            (root/"v22-market-catalog.json").write_text(
                json.dumps(catalog),encoding="utf8")
            replay.main(["--docs",str(root),"--mode","write"])
            replay.main(["--docs",str(root),"--mode","verify"])
            path=root/"v3-current-price-technical-momentum.json"
            observed=json.loads(path.read_text(encoding="utf8"))
            observed["certified_100point_investment_issuers"]=1
            path.write_text(json.dumps(observed),encoding="utf8")
            with self.assertRaises(replay.CurrentPriceReplayError):
                replay.main(["--docs",str(root),"--mode","verify"])


if __name__=="__main__":
    unittest.main()
