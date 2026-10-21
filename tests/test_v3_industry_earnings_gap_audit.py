"""Industry-gap diagnostics must use saved evidence without 15-point guesses."""
import copy
import csv
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from v3_industry_earnings_gap_audit import (
    SCHEMA,CATEGORIES,missing_reason,build,missing_csv,main,IndustryGapError,
)
from v3_dart_industry_earnings_sample import build as sample_build,source_join_ready

DOCS=Path(__file__).resolve().parents[1]/"docs"

def saved():
    return [
        json.loads((DOCS/n).read_text(encoding="utf8")) for n in (
            "data.json","v22-market-catalog.json",
            "v3-dart-industry-classification.json",
            "v3-dart-bulk-history.json")
    ]


def statement(year,op=50,revenue=300,ambiguous=None,receipt=None):
    if receipt is None:
        receipt=f"{year+1}0318001162"
    return {"receipt_id":receipt,"values":{
      "operating_income":op,"revenue":revenue},
      "ambiguous":[] if ambiguous is None else ambiguous}


def issuer(old=None,new=None,scope="CFS"):
    return {"years":{
        "2024":{scope:statement(2024) if old is None else old},
        "2025":{scope:statement(2025) if new is None else new},
    }}


class IndustryEarningsMissingnessTests(unittest.TestCase):
    def test_exclusive_first_gap_reason_and_measured_zero_are_distinct(self):
        d="2026-10-21"
        first,last=2024,2025
        self.assertIsNone(missing_reason(issuer(
            old=statement(2024,op=0),new=statement(2025,op=-30)),
            first,last,d))
        examples={
            "saved_company_annual_history_absent":None,
            "saved_both_comparison_years_absent":{"years":{}},
            "saved_prior_year_absent":{"years":{"2025":{"CFS":statement(2025)}}},
            "saved_latest_year_absent":{"years":{"2024":{"CFS":statement(2024)}}},
            "shared_CFS_OFS_pair_absent":{"years":{
                "2024":{"CFS":statement(2024)},
                "2025":{"OFS":statement(2025)}}},
            "annual_account_ambiguity":issuer(
                old=statement(2024,ambiguous=["operating_income"])),
            "annual_receipt_not_verified":issuer(
                new=statement(2025,receipt="20200101000123")),
            "annual_operating_income_missing":issuer(
                old=statement(2024,op=None)),
            "annual_revenue_missing_or_nonpositive":issuer(
                new=statement(2025,revenue=0)),
            "annual_amount_outside_safeguards":issuer(
                old=statement(2024,op=10**19)),
            "unusable_saved_annual_detail":issuer(
                new={"receipt_id":"20260318001162","values":None,
                     "ambiguous":[]}),
        }
        self.assertEqual(set(examples),set(CATEGORIES))
        for label,rec in examples.items():
            with self.subTest(cause=label):
                self.assertEqual(
                    missing_reason(rec,first,last,d),label)

    def test_current_saved_real_source_reconciles_all_groups(self):
        market,catalog,industry,bulk=saved()
        if not source_join_ready(market,catalog,industry,bulk):
            self.skipTest("source_market_day_not_yet_rolled_to_latest_annual")
        result,missing=build(market,catalog,industry,bulk)
        sample=sample_build(market,catalog,industry,bulk)
        self.assertEqual(result["schema"],SCHEMA)
        self.assertEqual(result["listed_total"],catalog["listed_total"])
        self.assertEqual(result["saved_profit_pair_unavailable"],len(missing))
        self.assertEqual(result["actual_saved_annual_profit_pairs"],
            sample["mapped_industry_issuer_earnings_two_year_matched"])
        self.assertEqual(result["group_count"],
            sample["observed_2digit_code_market_groups"])
        self.assertEqual(sum(result["missing_reason_counts"].values()),
                         len(missing))
        self.assertEqual(result["source_calls"],0)
        self.assertFalse(result["actual_industry_15_points_authorized"])
        self.assertFalse(result["official_krx_sector_membership_pit_verified"])
        self.assertEqual(len({row["code"] for row in missing}),len(missing))
        self.assertTrue(all(row["saved_source_gap"] in CATEGORIES
                            for row in missing))
        output=list(csv.DictReader(StringIO(
            missing_csv(missing,result).lstrip("\ufeff"))))
        self.assertEqual(len(output),len(missing))
        self.assertTrue(all(x["산업15점투자점수"]==""
                            and x["과거KRX업종확인"]=="미확인" for x in output))
        for group in result["groups"]:
            self.assertEqual(group["classified"],group["compared"]+group["missing"])
            self.assertEqual(group["missing"],sum(group["gap_reason_counts"].values()))

    def test_forged_current_company_profile_and_future_history_fail(self):
        market,catalog,industry,bulk=saved()
        if not source_join_ready(market,catalog,industry,bulk):
            self.skipTest("independent_annual_source_not_same_day")
        altered=copy.deepcopy(industry)
        code=next(iter(altered["records"]))
        altered["records"][code]["industry_code"]="99999"
        with self.assertRaises(IndustryGapError):
            build(market,catalog,altered,bulk)
        altered=copy.deepcopy(bulk)
        altered["raw_source_rows_published"]=True
        with self.assertRaises(IndustryGapError):
            build(market,catalog,industry,altered)
        altered=copy.deepcopy(bulk)
        altered["asof"]="2099-10-21"
        with self.assertRaises(Exception):
            build(market,catalog,industry,altered)
        altered=copy.deepcopy(industry)
        altered["industry_5433_points_authorized"]=True
        with self.assertRaises(IndustryGapError):
            build(market,catalog,altered,bulk)

    def test_cli_writes_both_outputs_verifies_and_rejects_tamper(self):
        market,catalog,industry,bulk=saved()
        if not source_join_ready(market,catalog,industry,bulk):
            self.skipTest("annual_source_not_same_day")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for file,source in zip((
                 "data.json","v22-market-catalog.json",
                 "v3-dart-industry-classification.json",
                 "v3-dart-bulk-history.json"),
                 (market,catalog,industry,bulk)):
                (root/file).write_text(json.dumps(source),encoding="utf8")
            main(["--docs",str(root),"--mode","write"])
            main(["--docs",str(root),"--mode","verify"])
            summary=root/"v3-industry-saved-profit-gap-summary.json"
            parsed=json.loads(summary.read_text(encoding="utf8"))
            parsed["actual_industry_15_points_authorized"]=True
            summary.write_text(json.dumps(parsed),encoding="utf8")
            with self.assertRaises(IndustryGapError):
                main(["--docs",str(root),"--mode","verify"])

    def test_workflow_never_calls_provider_or_forces_git(self):
        path=Path(__file__).resolve().parents[1]
        for html in ("index.html","workspace.html"):
            body=(path/"docs"/html).read_text(encoding="utf8")
            self.assertIn('href="v3-industry-saved-profit-gap-issuers.csv"',body)
            self.assertIn("공식 KRX 산업 15점이 아닙니다",body)
        flow=(path/".github/workflows/v3-industry-earnings-gap.yml").read_text("utf8")
        self.assertIn("v3_industry_earnings_gap_audit.py --mode write",flow)
        self.assertIn("v3_industry_earnings_gap_audit.py --mode verify",flow)
        self.assertIn("v3-industry-saved-profit-gap-issuers.csv",flow)
        self.assertIn("group: korea-stock-screener-industry-gap",flow)
        self.assertNotIn("DART_API_KEY",flow)
        self.assertNotIn("KRX_API_KEY",flow)
        self.assertNotIn("git push --force",flow)


if __name__=="__main__":
    unittest.main()
