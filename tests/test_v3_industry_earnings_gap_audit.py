"""Industry-gap diagnostics must use saved evidence without 15-point guesses."""
import copy
import csv
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from v3_industry_earnings_gap_audit import (
    SCHEMA,CATEGORIES,missing_reason,verified_operating_income_pair_without_revenue,
    build,missing_csv,industry_breadth_csv,main,IndustryGapError,
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

    def test_profit_only_independent_proof_does_not_infer_revenue(self):
        d="2026-10-21"
        valid=issuer(old=statement(2024,op=0,revenue=None),
                     new=statement(2025,op=-15,revenue=0))
        proven=verified_operating_income_pair_without_revenue(valid,2024,2025,d)
        self.assertEqual(proven,("CFS",0,-15))
        self.assertEqual(missing_reason(valid,2024,2025,d),
                         "annual_revenue_missing_or_nonpositive")
        no_receipt=issuer(old=statement(2024,revenue=0),
                          new=statement(2025,receipt="20210318001162"))
        self.assertIsNone(verified_operating_income_pair_without_revenue(
            no_receipt,2024,2025,d))
        ambiguous=issuer(old=statement(2024,ambiguous=["operating_income"]))
        self.assertIsNone(verified_operating_income_pair_without_revenue(
            ambiguous,2024,2025,d))
        absent=issuer(old=statement(2024,op=None))
        self.assertIsNone(verified_operating_income_pair_without_revenue(
            absent,2024,2025,d))
        mismatch={"years":{"2024":{"CFS":statement(2024)},
                           "2025":{"OFS":statement(2025)}}}
        self.assertIsNone(verified_operating_income_pair_without_revenue(
            mismatch,2024,2025,d))
        invalid=issuer(old=statement(2024,op=True))
        self.assertIsNone(verified_operating_income_pair_without_revenue(
            invalid,2024,2025,d))

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
        rescued=result["verified_operating_profit_only_pair_among_missing"]
        self.assertEqual(rescued,
            sum(row["operating_profit_pair_without_revenue_verified"] for row in missing))
        self.assertEqual(rescued,
            sum(result["profit_only_among_missing_by_reason"].values()))
        self.assertEqual(rescued,
            sum(result["profit_only_among_missing_by_market"].values()))
        self.assertLessEqual(rescued,len(missing))
        comparable=result["two_year_operating_profit_verified_any_revenue"]
        self.assertEqual(comparable,result["actual_saved_annual_profit_pairs"]+
                         rescued)
        self.assertEqual(result["two_year_operating_profit_coverage_any_revenue"],
                         round(comparable/result["listed_total"],6))
        self.assertTrue(result[
            "two_year_profit_breadth_current_company_code_sample_only"])
        self.assertFalse(result["historical_sector_profit_breadth_certified"])
        self.assertTrue(result["profit_only_verified_but_full_pair_still_missing"])
        self.assertEqual(result["actual_saved_annual_profit_pairs"]+
                         result["saved_profit_pair_unavailable"],len(catalog["rows"]))
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
                            and x["과거KRX업종확인"]=="미확인"
                            and x["매출포함_엄격비교"]=="미확보"
                            and x["영업이익_2개년_매출무관_별도검증"] in
                               ("검증","미검증") for x in output))
        self.assertEqual(sum(x["영업이익_2개년_매출무관_별도검증"]=="검증"
                             for x in output),rescued)
        for group in result["groups"]:
            self.assertEqual(group["classified"],group["compared"]+group["missing"])
            self.assertEqual(group["missing"],sum(group["gap_reason_counts"].values()))
        self.assertEqual(sum(x["verified_profit_only_gap_count"]
                             for x in result["groups"]),rescued)
        self.assertEqual(sum(x["two_year_operating_profit_verified_issuers"]
                             for x in result["groups"]),comparable)
        original={(r["market"],r["dart_industry_code_prefix_2"]):r
                  for r in sample["groups"]}
        for row in result["groups"]:
            n=row["two_year_operating_profit_verified_issuers"]
            self.assertEqual(n,row["compared"]+
                             row["verified_profit_only_gap_count"])
            self.assertLessEqual(n,row["classified"])
            count=row["two_year_operating_profit_improving_issuers"]
            rate=row["two_year_profit_improving_ratio_observed_sample_only"]
            low=row["current_code_improving_breadth_lower_bound"]
            high=row["current_code_improving_breadth_upper_bound"]
            if n<3:
                self.assertIsNone(count)
                self.assertIsNone(rate)
                self.assertIsNone(low)
                self.assertIsNone(high)
                continue
            self.assertLessEqual(0,count)
            self.assertLessEqual(count,n)
            self.assertEqual(rate,round(count/n,6))
            self.assertLessEqual(low,rate+0.000002)
            self.assertGreaterEqual(high+0.000002,rate)
            if row["verified_profit_only_gap_count"]==0:
                reference=original[(row["market"],row["industry_code_prefix_2"])]
                self.assertEqual(rate,reference[
                    "observed_earnings_improving_issuer_ratio_sample_only"])
        # Still no industry 15/100 certification or fabricated historical roster.
        self.assertEqual(result["actual_industry_15_points_authorized"],False)
        self.assertNotIn("issuer_operating_profit_amounts",json.dumps(result))

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

    def test_last_published_industry_breadth_replays_without_current_day(self):
        # The current market may roll to tomorrow before 4-year DART catches up.
        # This independent test must never SKIP: it validates the LAST published
        # source-audited group snapshot, with the ORIGINAL asof, without APIs.
        previous=json.loads((DOCS/"v3-industry-saved-profit-gap-summary.json")
                            .read_text(encoding="utf8"))
        result=industry_breadth_csv(previous)
        stored=(DOCS/"v3-industry-observed-profit-breadth.csv").read_text(
            encoding="utf8")
        self.assertEqual(result,stored)
        values=list(csv.DictReader(StringIO(result.removeprefix("\ufeff"))))
        self.assertEqual(len(values),previous["group_count"])
        self.assertEqual({x["공시자료기준일"] for x in values},{previous["asof"]})
        self.assertEqual(sum(int(x["영업이익2개년_검증기업수"]) for x in values),
                         previous["two_year_operating_profit_verified_any_revenue"])
        self.assertTrue(all(x["공식산업15점"]=="" for x in values))
        self.assertTrue(all(x["과거KRX업종구성_PIT"]=="미확인" for x in values))
        bad=copy.deepcopy(previous)
        bad["groups"][0]["two_year_operating_profit_verified_issuers"]+=1
        with self.assertRaises(IndustryGapError):
            industry_breadth_csv(bad)

    def test_observed_industry_breadth_excel_csv_preserves_missingness(self):
        market,catalog,industry,bulk=saved()
        if not source_join_ready(market,catalog,industry,bulk):
            self.skipTest("independent_annual_source_not_same_day")
        stats,_=build(market,catalog,industry,bulk)
        body=industry_breadth_csv(stats)
        self.assertTrue(body.startswith("\ufeff"))
        items=list(csv.DictReader(StringIO(body.removeprefix("\ufeff"))))
        self.assertEqual(len(items),stats["group_count"])
        self.assertEqual(sum(int(x["현재상장기업수"]) for x in items),
                         stats["listed_total"])
        self.assertEqual(sum(int(x["영업이익2개년_검증기업수"]) for x in items),
                         stats["two_year_operating_profit_verified_any_revenue"])
        matched={(x["시장"],x["현재DART업종코드앞2자리"]):x for x in items}
        for group in stats["groups"]:
            x=matched[(group["market"],group["industry_code_prefix_2"])]
            self.assertEqual(x["산업15점투자점수"] if "산업15점투자점수" in x else x["공식산업15점"],"")
            self.assertEqual(x["과거KRX업종구성_PIT"],"미확인")
            count=group["two_year_operating_profit_verified_issuers"]
            if count<3:
                self.assertEqual(x["영업이익개선기업수_표본3개이상"],"")
                self.assertEqual(x["검증표본내_실적개선율_%"],"")
            else:
                self.assertAlmostEqual(
                    float(x["검증표본내_실적개선율_%"]),
                    round(100*group["two_year_profit_improving_ratio_observed_sample_only"],2),
                    places=2)
        forged=copy.deepcopy(stats)
        forged["actual_industry_15_points_authorized"]=True
        with self.assertRaises(IndustryGapError):
            industry_breadth_csv(forged)
        forged=copy.deepcopy(stats)
        forged["groups"][0]["classified"]+=1
        with self.assertRaises(IndustryGapError):
            industry_breadth_csv(forged)

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
            breadth=root/"v3-industry-observed-profit-breadth.csv"
            self.assertGreater(len(breadth.read_text(encoding="utf8")),100)
            breadth.write_text("tampered",encoding="utf8")
            with self.assertRaises(IndustryGapError):
                main(["--docs",str(root),"--mode","verify"])
            main(["--docs",str(root),"--mode","write"])
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
