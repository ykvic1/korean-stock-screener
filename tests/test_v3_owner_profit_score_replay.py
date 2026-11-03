"""A real exact FY2025 issuer+filing numerator can complete provisional 95->100.

No DART/KRX network, no fake certification. Rebuild actual market-wide
percentiles using independently saved annual baseline and a synthetic
verified-owner-income evidence record, and leave missing IFRS accounts null.
"""
import copy
import json
import contextlib
import io
import tempfile
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

    def fiscal_options(self,**kwargs):
        # Combined audit can lag the official same-day DART source.
        if self.audit.get("market_asof") != self.catalog["date"]:
            return dict(weekly=read("v3-weekly-rotation.json"),**kwargs)
        return kwargs

    def test_exact_proof_replays_market_peers_into_real_95_weight_candidate(self):
        source=self.proof()
        old=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,**self.fiscal_options())
        new=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,
            owner_profit_source=source,owner_profit_priority=self.priority,**self.fiscal_options())
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
            self.catalog,self.history,self.parts,self.annual,self.audit,**self.fiscal_options())
        suplement=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,
            owner_profit_source=self.proof("-123456789"),
            owner_profit_priority=self.priority,**self.fiscal_options())
        prices=price_build(self.market,self.catalog)
        volumes=volume_build(self.market,self.catalog)
        cash=read("v3-dart-cashflow-reference.json")
        preview=read("v3-dart-fcf-research-preview.json")
        replayed=cashflow_source(
            self.market,self.catalog,self.history,cash,preview)
        self.assertIsNotNone(replayed)
        fcf,_=replayed
        from v3_unscored_path_coverage import summarize as path_audit
        source_path=(path_audit(self.market)
                     if self.market.get("v3_unscored_path_source") is not None
                     else None)
        _,base_summary=generate(self.market,self.catalog,volumes,prices,
                                financial=original,cashflow=fcf,
                                path_coverage_receipt=source_path)
        rows,summary=generate(self.market,self.catalog,volumes,prices,
                              financial=suplement,cashflow=fcf,
                              path_coverage_receipt=source_path)
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

    def test_publisher_gap_refresh_and_second_publication_retain_real_receipts(self):
        from v3_current_observed_scorecard import main as publish
        from v3_near_100_observed_gap import main as gap_audit
        from v3_dart_2025_owner_profit_probe import verify
        outputs = {
            "v3-current-observed-all-listed.csv",
            "v3-current-observed-all-listed-summary.json",
            "v3-issuer-last-observed-factors.json",
            "v3-near-100-evidence-priority.json",
            "v3-dart-owner-profit-supplement.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in DOCS.iterdir():
                if path.is_file():
                    if path.name in outputs:
                        (root/path.name).write_bytes(path.read_bytes())
                    else:
                        (root/path.name).symlink_to(path)
            source = self.proof()
            (root/"v3-dart-owner-profit-supplement.json").write_text(
                json.dumps(source), encoding="utf8")
            with contextlib.redirect_stdout(io.StringIO()):
                publish(["--docs", str(root), "--mode", "write", "--refresh-derived"])
                gap_audit(["--docs", str(root), "--mode", "write"])
                first = {name:(root/name).read_bytes() for name in outputs}
                publish(["--docs", str(root), "--mode", "verify", "--refresh-derived"])
                publish(["--docs", str(root), "--mode", "write", "--refresh-derived"])
                gap_audit(["--docs", str(root), "--mode", "verify"])
            self.assertEqual(first, {name:(root/name).read_bytes() for name in outputs})
            updated = json.loads((root/"v3-near-100-evidence-priority.json").read_text())
            self.assertIn(self.code, updated["official_2025_owners_profit_missing_priority_codes"])
            baseline = updated
            self.assertEqual(baseline, self.priority)
            self.assertTrue(verify(source, self.catalog, self.annual, baseline))
            _, _, _, retained, selected = plan(
                self.catalog, self.annual, baseline, source, limit=20)
            self.assertIn(self.code, retained)
            self.assertNotIn(self.code, selected)


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
            owner_profit_source=source,owner_profit_priority=self.priority,**self.fiscal_options())
        original=finance_source(
            self.catalog,self.history,self.parts,self.annual,self.audit,**self.fiscal_options())
        self.assertEqual(changed,original)

    def test_altered_profit_or_receipt_source_fails_closed(self):
        source=self.proof()
        tampered=copy.deepcopy(source)
        tampered["records"][self.code]["annual_receipt_id"]="20260101000001"
        with self.assertRaisesRegex(
                LivePartialScorecardError,"untrusted_official_owner_profit_supplement"):
            finance_source(
                self.catalog,self.history,self.parts,self.annual,self.audit,
                owner_profit_source=tampered,owner_profit_priority=self.priority,**self.fiscal_options())
        modified=copy.deepcopy(source)
        modified["investment_grade_score_certified"]=True
        with self.assertRaisesRegex(
                LivePartialScorecardError,"untrusted_official_owner_profit_supplement"):
            finance_source(
                self.catalog,self.history,self.parts,self.annual,self.audit,
                owner_profit_source=modified,owner_profit_priority=self.priority,**self.fiscal_options())


if __name__ == "__main__":
    unittest.main()
