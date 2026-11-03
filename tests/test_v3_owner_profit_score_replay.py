"""A real exact FY2025 issuer+filing numerator can complete provisional 95->100.

No DART/KRX network, no fake certification. Rebuild actual market-wide
percentiles using independently saved annual baseline and a synthetic
verified-owner-income evidence record, and leave missing IFRS accounts null.
"""
import copy
import json
from pathlib import Path
import unittest

from v3_current_observed_scorecard import (
    finance_source, cashflow_source, generate, LivePartialScorecardError,
)
from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build
from v3_dart_2025_owner_profit_probe import (
    plan, parse_response, snapshot,
)

DOCS=Path(__file__).resolve().parents[1]/"docs"


def read(name):
    return json.loads((DOCS/name).read_text(encoding="utf8"))


class OwnerProfitScoreReplayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=read("v22-market-catalog.json")
        cls.market=read("data.json")
        cls.annual=read("v3-dart-book-reference.json")
        cls.history=read("v3-dart-bulk-history.json")
        cls.parts=read("v3-dart-bulk-provisional.json")
        cls.audit=read("v3-real-source-six-area-coverage.json")
        cls.priority=read("v3-near-100-evidence-priority.json")
        cls.day,_,cls.expected,_,cls.selected=plan(
            cls.catalog,cls.annual,cls.priority,None,limit=1)
        cls.code=cls.selected[0]
        cls.base=cls.expected[cls.code]

    def proof(self, amount="123456789"):
        code=self.code
        a=self.base
        scope=a["fs_div"]
        account=("ifrs-full_ProfitLossAttributableToOwnersOfParent"
                 if scope=="CFS" else "ifrs-full_ProfitLoss")
        row={
            "corp_code":"12345678","stock_code":code,
            "bsns_year":"2025","reprt_code":"11011",
            "fs_div":scope,"sj_div":"IS","account_detail":"-",
            "currency":"KRW","rcept_no":a["annual_receipt_id"],
            "account_id":account,"thstrm_amount":amount,
        }
        entry=parse_response({"status":"000","list":[row]},
                             corp="12345678",base=a,on_day=self.day)
        return snapshot(self.day,self.catalog,self.annual,self.priority,
                        {code:entry})

    def test_exact_proof_replays_market_peers_into_real_95_weight_candidate(self):
        source=self.proof()
        old=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit)
        new=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,
            owner_profit_source=source,owner_profit_priority=self.priority)
        code=self.code
        self.assertIsNotNone(old)
        self.assertEqual(old[code]["valuation_available_max"],10)
        self.assertEqual(new[code]["valuation_available_max"],15)
        self.assertIsNone(old[code]["unverified_components"]
                          ["earnings_yield_pct"]["candidate_points"])
        self.assertEqual(new[code]["unverified_components"]
                         ["earnings_yield_pct"]["max_points"],5)
        self.assertTrue(new[code]["owner_profit_2025_source_replayed"])
        self.assertFalse(new[code]["scoring_certified"])
        self.assertEqual(new[code]["confirmed_v3_score"],None)
        self.assertEqual(new[code]["asof"],self.day)

    def test_actual_full_scorecard_has_one_new_100weight_but_zero_certified(self):
        original=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit)
        suplement=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,
            owner_profit_source=self.proof("-123456789"),
            owner_profit_priority=self.priority)
        prices=price_build(self.market,self.catalog)
        volumes=volume_build(self.market,self.catalog)
        cash=read("v3-dart-cashflow-reference.json")
        preview=read("v3-dart-fcf-research-preview.json")
        replayed=cashflow_source(
            self.market,self.catalog,self.history,cash,preview)
        self.assertIsNotNone(replayed)
        fcf,_=replayed
        _,base_summary=generate(self.market,self.catalog,volumes,prices,
                                financial=original,cashflow=fcf)
        rows,summary=generate(self.market,self.catalog,volumes,prices,
                              financial=suplement,cashflow=fcf)
        target=next(x for x in rows if x["code"]==self.code)
        self.assertEqual(target["available"],100)
        self.assertIsNone(target["official_score"])
        self.assertFalse(target["certified"])
        self.assertEqual(
            summary["provisional_complete_100point_observation_count"],
            base_summary["provisional_complete_100point_observation_count"]+1)
        self.assertEqual(
            summary["observed_100weight_issuers_with_2025_owner_profit_supplement"],1)
        self.assertEqual(
            summary["owner_profit_2025_official_receipt_replayed_issuers"],1)
        self.assertFalse(summary["owner_profit_supplement_investment_grade_certified"])
        self.assertEqual(summary["certified_100point_count"],0)

    def test_missing_account_does_not_activate_five_weight(self):
        source=self.proof()
        source["records"][self.code].update({
            "status":"missing_standard_account",
            "owners_profit_krw":None,"source_account_id":None,
        })
        source=snapshot(self.day,self.catalog,self.annual,self.priority,
                        source["records"])
        changed=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,
            owner_profit_source=source,owner_profit_priority=self.priority)
        original=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit)
        self.assertEqual(changed,original)

    def test_altered_profit_or_receipt_source_fails_closed(self):
        source=self.proof()
        tampered=copy.deepcopy(source)
        tampered["records"][self.code]["annual_receipt_id"]="20260101000001"
        with self.assertRaisesRegex(
                LivePartialScorecardError,"untrusted_official_owner_profit_supplement"):
            finance_source(
                self.catalog,self.history,self.parts,self.annual,self.audit,
                owner_profit_source=tampered,owner_profit_priority=self.priority)
        modified=copy.deepcopy(source)
        modified["investment_grade_score_certified"]=True
        with self.assertRaisesRegex(
                LivePartialScorecardError,"untrusted_official_owner_profit_supplement"):
            finance_source(
                self.catalog,self.history,self.parts,self.annual,self.audit,
                owner_profit_source=modified,owner_profit_priority=self.priority)


if __name__ == "__main__":
    unittest.main()
