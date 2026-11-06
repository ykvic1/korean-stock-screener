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
        # Full saved-source integration requires an aligned closing snapshot.
        # Parser/receipt/lag rejection contracts run on deterministic fixtures
        # in test_v3_dart_2025_owner_profit_probe even during this wait.
        from datetime import date
        day=date.fromisoformat(cls.catalog["date"])
        if any(date.fromisoformat(source["asof"]) < day
               for source in (cls.annual,cls.priority)):
            raise unittest.SkipTest("waiting for same-day annual and priority sources")
        saved=read("v3-dart-owner-profit-supplement.json")
        cls.day,_,cls.expected,cls.retained,cls.selected=plan(
            cls.catalog,cls.annual,cls.priority,saved,limit=1)
        # All-listed admission means the first queue entry need not have
        # ten observed valuation points. Pick the explicit legacy test
        # condition instead of assuming sorted acquisition order implies it.
        added=set(cls.priority.get('r1d_all_listed_appended_codes',[]))
        partial={r['code']:r for r in cls.parts['rows']}
        candidates=[c for c in sorted(cls.expected) if c not in added and
                    partial.get(c,{}).get('valuation_available_max')==10]
        if not candidates:raise unittest.SkipTest('no current legacy ten-weight valuation fixture')
        cls.code=candidates[0]
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
        records=copy.deepcopy(self.retained)
        records[code]=entry
        return snapshot(self.day,self.catalog,self.annual,self.priority,records)

    def original_finance(self):
        records=copy.deepcopy(self.retained)
        records.pop(self.code,None)
        source=snapshot(self.day,self.catalog,self.annual,self.priority,records)
        return finance_source(self.catalog,self.history,self.parts,self.annual,self.audit,
            owner_profit_source=source,owner_profit_priority=self.priority,**self.fiscal_options())

    def fiscal_options(self,**kwargs):
        # Combined audit can lag the official same-day DART source.
        if self.audit.get("market_asof") != self.catalog["date"]:
            return dict(weekly=read("v3-weekly-rotation.json"),**kwargs)
        return kwargs

    def test_exact_proof_replays_market_peers_into_real_95_weight_candidate(self):
        source=self.proof()
        old=self.original_finance()
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

    def test_owner_profit_adds_five_without_falsely_restoring_removed_obv_six(self):
        original=self.original_finance()
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
        base_rows,base_summary=generate(self.market,self.catalog,volumes,prices,
                                         financial=original,cashflow=fcf,
                                         path_coverage_receipt=source_path)
        rows,summary=generate(self.market,self.catalog,volumes,prices,
                              financial=suplement,cashflow=fcf,
                              path_coverage_receipt=source_path)
        target=next(x for x in rows if x["code"]==self.code)
        baseline=next(x for x in base_rows if x["code"]==self.code)
        # The verified FY2025 owner profit still adds its five observed
        # valuation points. The removed OBV-rise bonus must NOT silently
        # fill missing six-point MA-cross history on the real saved source.
        self.assertEqual(target["available"],baseline["available"]+5)
        self.assertIn(target["available"],(94,100))
        self.assertIsNone(target["official_score"])
        self.assertFalse(target["certified"])
        new_full=int(target["available"]==100)
        self.assertEqual(
            summary["provisional_complete_100point_observation_count"],
            base_summary["provisional_complete_100point_observation_count"]+new_full)
        self.assertEqual(
            summary["observed_100weight_issuers_with_2025_owner_profit_supplement"],
            base_summary["observed_100weight_issuers_with_2025_owner_profit_supplement"]+new_full)
        self.assertEqual(
            summary["owner_profit_2025_official_receipt_replayed_issuers"],
            self.proof()["observed_official_owner_profit_issuers"])
        self.assertFalse(summary["owner_profit_supplement_investment_grade_certified"])
        self.assertEqual(summary["certified_100point_count"],0)

    def test_publisher_gap_refresh_and_second_publication_retain_real_receipts(self):
        from v3_current_observed_scorecard import main as publish
        from v3_near_100_observed_gap import main as gap_audit
        from v3_dart_2025_owner_profit_probe import verify
        outputs = {
            "v3-personal-adjusted-scores.csv",
            "v3-personal-adjusted-score-summary.json",
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
        original=self.original_finance()
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
