"""Offline mandatory PIT and event quality blocker with real workbench path."""
from __future__ import annotations
import copy
import unittest

from v3_mandatory_quality_gate import check_research_quality, InputQualityError
from v3_offline_workbench import demo_bundle, run_workbench


def evidence(stock):
    ident = {x: stock[x] for x in ("code", "issuer_id", "market")}
    asof = stock["asof"]
    observed = "2026-10-16T06:40:00Z"
    pit = {
        **ident,
        "roster": {"asof": asof, "observed_at_utc": observed,
                   "members": [ident["issuer_id"]]},
        "price": {**ident, "asof": asof,
                  "session": "KRX_REGULAR_0900_1530_ASIA_SEOUL",
                  "raw_sha256": "a"*64, "observed_at_utc": observed},
        "flow_ledger": {**ident, "asof": asof, "observed_at_utc": observed,
                        "ledger": stock["flow_ledger"]},
        "filings": [{**ident, "rcept_no": "20260301000001",
                      "period_end": "2025-12-31", "scope": "CFS", "revision_no": 0,
                      "source_sha256": "b"*64,
                      "published_at_utc": "2026-03-01T01:00:00Z",
                      "observed_at_utc": "2026-03-01T02:00:00Z"}],
        "sector": {**ident, "sector_code": "IND.01", "valid_from": "2023-01-01",
                   "observed_at_utc": observed, "valid_to": None},
    }
    record = {**ident, "listed_on": "2020-01-01", "delisted_on": None,
              "events": [], "bars": [{**row, "trading_status": "active"}
                                    for row in stock["price_bars"]]}
    return {"pit_bundle": pit, "price_record": record,
            "decision_at_utc": "2026-10-16T07:10:00Z"}


class MandatoryQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = demo_bundle()
        cls.stock = cls.demo["stocks"][0]

    def runcheck(self, evidence_in):
        stock = self.stock
        return check_research_quality(
            asof=stock["asof"], code=stock["code"],
            issuer_id=stock["issuer_id"], market=stock["market"],
            price_bars=stock["price_bars"], evidence=evidence_in)

    def test_missing_evidence_blocks_but_does_not_invent_data(self):
        r = self.runcheck(None)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("missing_historical_pit_and_event_evidence", r["blocking_reasons"])
        self.assertFalse(r["independently_authenticated"])
        self.assertFalse(r["eligible_for_confirmed_scoring"])
        self.assertFalse(r["eligible_for_real_backtesting"])
        self.assertFalse(r["confirmed_buy"])

    def test_valid_internal_same_session_still_cannot_claim_authenticated(self):
        r = self.runcheck(evidence(self.stock))
        self.assertEqual(r["status"], "internally_aligned_not_authenticated")
        self.assertTrue(r["historical_evidence_structurally_aligned"])
        self.assertFalse(r["independently_authenticated"])
        self.assertFalse(r["eligible_for_confirmed_scoring"])

    def test_later_filing_or_flow_blocks_asof_not_retroactive_data(self):
        ev = evidence(self.stock)
        ev["pit_bundle"]["flow_ledger"]["observed_at_utc"] = "2026-10-17T01:00:00Z"
        r = self.runcheck(ev)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("snapshot_not_available_at_decision", r["blocking_reasons"])

    def test_mismatched_price_evidence_and_wrong_issuer_are_not_silent(self):
        ev = evidence(self.stock)
        ev["price_record"]["bars"][-1]["close"] += 1
        with self.assertRaisesRegex(InputQualityError, "price_scoring_and_evidence_disagree"):
            self.runcheck(ev)
        ev = evidence(self.stock)
        ev["pit_bundle"]["issuer_id"] = "99999999"
        with self.assertRaisesRegex(InputQualityError, "quality_issuer_or_market_mismatch"):
            self.runcheck(ev)

    def test_split_quarantine_blocks_research_but_no_false_certification(self):
        ev = evidence(self.stock)
        ev["price_record"]["events"] = [
            {"effective_date": self.stock["price_bars"][-2]["date"],
             "kind": "split", "source_sha256": "c"*64}]
        r = self.runcheck(ev)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("signal_near_unverified_corporate_event", r["blocking_reasons"])

    def test_workbench_integrates_quality_for_every_stock_not_score_override(self):
        report = run_workbench(self.demo)
        self.assertEqual(report["research_stock_count"], 64)
        self.assertEqual(report["input_issue_counts"]["quality_blocked"], 64)
        self.assertEqual(report["fully_covered_six_pillar_research_only"], 64)
        self.assertTrue(all(
            s["mandatory_quality_gate"]["eligible_for_confirmed_scoring"] is False
            and s["eligible_for_verified_v3"] is False
            and s["trade_approval"] is False
            for s in report["stocks_in_code_order_not_ranked"]))
        self.assertFalse(report["v3_release_ready"])

    def test_workbench_quality_one_internally_aligned_and_remainder_blocked(self):
        data = copy.deepcopy(self.demo)
        data["stocks"][0]["quality_evidence"] = evidence(data["stocks"][0])
        r = run_workbench(data)
        self.assertEqual(r["input_issue_counts"]["quality_blocked"], 63)
        self.assertEqual(
            r["input_issue_counts"]["quality_internally_aligned_not_authenticated"], 1)
        self.assertFalse(r["stocks_in_code_order_not_ranked"][0]["eligible_for_verified_v3"])

    def test_false_caller_ready_claim_ignored(self):
        ev = evidence(self.stock)
        ev["certified"] = True
        ev["pit_bundle"]["confirmed_buy"] = True
        r = self.runcheck(ev)
        self.assertFalse(r["confirmed_buy"])
        self.assertFalse(r["independently_authenticated"])


if __name__ == "__main__":
    unittest.main()
