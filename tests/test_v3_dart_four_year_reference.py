"""No-network synthetic four-year DART statement enrichment regression."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest

import v3_dart_four_year_reference as f
import v3_dart_rolling_book_reference as rolling
from test_v3_dart_rolling_book_reference import fixture, report, DAY


def reports(code, *, omit=(), scope="CFS"):
    out=[]
    for idx, yr in enumerate((2022,2023,2024,2025)):
        if yr in omit:
            continue
        revenue=100_000_000_000 * 1.1**idx
        out.append({
            "year":yr, "fs_div":scope,"currency":"KRW",
            "receipt_id":f"{yr+1}0318001162",
            "values":{
                "revenue":round(revenue),
                "operating_income":round(revenue*0.1),
                "operating_cashflow":10_000_000_000,
                "liabilities":20_000_000_000,
                "equity":30_000_000_000,
                "owners_equity":25_000_000_000,
                "owners_profit":3_000_000_000,
            }
        })
    return {"code":code,"status":"observed" if out else "unconfirmed",
            "reports":out}


def inputs():
    market, catalog=fixture()
    annual=rolling.merge(market,catalog,None,[
        report("005930",scope="CFS"),
        report("247540",scope="OFS"),
    ],limit=2)
    return market,catalog,annual


class FourYearAnnualExpansionTests(unittest.TestCase):
    def test_existing_annual_coverage_targets_only_real_observed_priced_names(self):
        m,c,b=inputs()
        day,members,old,selected=f.plan(m,c,b,limit=2)
        self.assertEqual(day,DAY)
        self.assertEqual(selected,["005930","247540"])
        self.assertEqual(len(members),5)
        self.assertFalse(old)

    def test_fiscal2022_2025_same_scope_cagr_and_margin_only(self):
        m,c,b=inputs()
        result=f.merge(m,c,b,[reports("005930"),reports("247540",scope="OFS")],limit=2)
        self.assertEqual(result["four_consecutive_years_observed_count"],2)
        self.assertEqual(result["four_year_income_values_observed_count"],2)
        self.assertEqual(result["last_three_years_operating_cashflow_observed_count"],2)
        self.assertEqual(result["latest_year_balance_values_observed_count"],2)
        self.assertEqual(result["four_year_core_financial_research_observed_count"],2)
        self.assertEqual(result["recent_four_year_window_observed_count"],2)
        self.assertEqual(result["current_core_financial_research_observed_count"],2)
        self.assertFalse(result["full_v3_ready"])
        self.assertIsNone(result["confirmed_six_pillar_ranks"])
        first=result["records"][0]
        self.assertEqual(first["observed_fiscal_years"],[2022,2023,2024,2025])
        self.assertEqual(first["accounting_scope"],"CFS")
        self.assertAlmostEqual(first["annual_revenue_cagr3_reference_pct"],10,places=3)
        self.assertAlmostEqual(first["three_year_operating_margin_mean_reference_pct"],10,places=4)
        for k in ("fcf_assets_reference_pct","net_debt_equity_reference_pct",
                  "valuation_official_pbr","financial15_confirmed_points",
                  "valuation15_confirmed_points","v3_full100_investable_score"):
            self.assertIsNone(first[k],k+" cannot be invented from DART 1-year values")
        self.assertFalse(first["four_year_financial_pit_authenticated"])
        self.assertFalse(first["historical_revisions_authenticated"])
        self.assertFalse(first["buy_certified"])
        self.assertFalse(result["raw_account_rows_stored"])

    def test_old_four_annual_filings_are_historical_not_current_company_financials(self):
        m,c,b=inputs()
        older=reports("005930")
        # 2021-2024 is a real consecutive four-fiscal-year window, but at
        # Oct 2026 its core financials are old. It cannot be considered a
        # current financial15 input or dropped from the next pilot cohort.
        for idx, item in enumerate(older["reports"]):
            yr=2021+idx
            item["year"]=yr
            item["receipt_id"]=f"{yr+1}0318001162"
        recent=reports("247540")
        result=f.merge(m,c,b,[older,recent],limit=2)
        self.assertEqual(result["four_consecutive_years_observed_count"],2)
        self.assertEqual(result["four_year_core_financial_research_observed_count"],2)
        self.assertEqual(result["recent_four_year_window_observed_count"],1)
        self.assertEqual(result["current_core_financial_research_observed_count"],1)
        r_old,r_new=result["records"]
        self.assertTrue(r_old["core_financial_observed_for_research"])
        self.assertFalse(r_old["recent_four_year_window_observed"])
        self.assertFalse(r_old["current_core_financial_research_observed"])
        self.assertEqual(r_old["minimum_current_annual_fiscal_year"],2025)
        self.assertEqual(r_old["latest_annual_fiscal_year_observed"],2024)
        self.assertTrue(r_new["current_core_financial_research_observed"])
        # Only the stale issuer remains eligible for a bounded refresh.
        self.assertEqual(f.plan(m,c,b,prior=result,limit=2)[3],["005930"])
        self.assertFalse(result["full_v3_ready"])

    def test_march_year_end_grace_avoids_unnecessary_stale_flags(self):
        older=reports("005930")["reports"]
        for idx, item in enumerate(older):
            yr=2021+idx
            item["year"]=yr
            item["receipt_id"]=f"{yr+1}0318001162"
        march=f._record("005930",older,"2026-03-30")
        april=f._record("005930",older,"2026-04-01")
        self.assertEqual(march["minimum_current_annual_fiscal_year"],2024)
        self.assertTrue(march["recent_four_year_window_observed"])
        self.assertEqual(april["minimum_current_annual_fiscal_year"],2025)
        self.assertFalse(april["recent_four_year_window_observed"])
        self.assertTrue(march["current_core_financial_research_observed"])
        self.assertFalse(april["current_core_financial_research_observed"])

    def test_partial_history_or_mixed_cfs_ofs_never_graduates(self):
        m,c,b=inputs()
        missing=reports("005930",omit=(2023,))
        changed=reports("247540",scope="OFS")
        changed["reports"][0]["fs_div"]="CFS"
        result=f.merge(m,c,b,[missing,changed],limit=2)
        self.assertEqual(result["four_consecutive_years_observed_count"],0)
        self.assertEqual(result["four_year_core_financial_research_observed_count"],0)
        for row in result["records"]:
            self.assertIsNone(row["annual_revenue_cagr3_reference_pct"])
            self.assertIsNone(row["three_year_operating_margin_mean_reference_pct"])
            self.assertFalse(row["same_scope_four_years"])

    def test_missing_revenue_or_profit_must_keep_factor_partial(self):
        m,c,b=inputs()
        a=reports("005930")
        a["reports"][0]["values"]["revenue"]=None
        d=reports("247540")
        d["reports"][-1]["values"]["operating_income"]=None
        results=f.merge(m,c,b,[a,d],limit=2)
        first,second=results["records"]
        self.assertEqual(results["four_consecutive_years_observed_count"],2)
        self.assertEqual(results["four_year_core_financial_research_observed_count"],0)
        self.assertFalse(first["four_year_income_values_observed"])
        self.assertFalse(second["four_year_income_values_observed"])
        self.assertFalse(first["core_financial_observed_for_research"])
        self.assertFalse(second["core_financial_observed_for_research"])
        self.assertIsNone(first["annual_revenue_cagr3_reference_pct"])
        self.assertAlmostEqual(first["three_year_operating_margin_mean_reference_pct"],10,places=3)
        self.assertAlmostEqual(second["annual_revenue_cagr3_reference_pct"],10,places=3)
        self.assertIsNone(second["three_year_operating_margin_mean_reference_pct"])

    def test_four_filing_receipts_do_not_imply_complete_financial_statements(self):
        m,c,b=inputs()
        empty=reports("005930")
        for record in empty["reports"]:
            record["values"]={field:None for field in f.FIELDS}
        weak=reports("247540")
        weak["reports"][2]["values"]["operating_cashflow"]=None
        weak["reports"][-1]["values"]["equity"]=None
        result=f.merge(m,c,b,[empty,weak],limit=2)
        self.assertEqual(result["four_consecutive_years_observed_count"],2)
        self.assertEqual(result["four_year_income_values_observed_count"],1)
        self.assertEqual(result["last_three_years_operating_cashflow_observed_count"],0)
        self.assertEqual(result["latest_year_balance_values_observed_count"],0)
        self.assertEqual(result["four_year_core_financial_research_observed_count"],0)
        self.assertTrue(all(not r["core_financial_observed_for_research"]
                            for r in result["records"]))
        self.assertTrue(all(r["financial15_confirmed_points"] is None
                            for r in result["records"]))
        self.assertTrue(all(r["valuation15_confirmed_points"] is None
                            for r in result["records"]))
        # Both must remain selectable for a recheck. Once income/CF/balance
        # evidence is present, a repeat request would no longer be justified.
        _,_,cached,retry=f.plan(m,c,b,prior=result,limit=2)
        self.assertEqual(set(retry),{"005930","247540"})
        self.assertEqual(len(cached),2)

    def test_future_receipt_bad_scope_duplicates_nonKRW_and_bogus_amount_rejected(self):
        m,c,b=inputs()
        cases={
            "future_receipt":lambda x:x["reports"][-1].update(receipt_id="20271018001162"),
            "before_end":lambda x:x["reports"][0].update(receipt_id="20221218001162"),
            "duplicate_year":lambda x:x["reports"][0].update(year=2023),
            "bad_scope":lambda x:x["reports"][0].update(fs_div="UNKNOWN"),
            "usd":lambda x:x["reports"][0].update(currency="USD"),
            "float_nan":lambda x:x["reports"][0]["values"].update(revenue=float("nan")),
            "bool_cash":lambda x:x["reports"][0]["values"].update(revenue=True),
            "big_amount":lambda x:x["reports"][0]["values"].update(revenue=10**40),
            "invalid_receipt":lambda x:x["reports"][0].update(receipt_id="20231318001162"),
        }
        for label,edit in cases.items():
            with self.subTest(label=label):
                q=reports("005930");edit(q)
                with self.assertRaises(f.FourYearEvidenceError):
                    f.merge(m,c,b,[q,reports("247540")],limit=2)

    def test_no_new_provider_workflow_or_over_budget_live_queries(self):
        m,c,b=inputs()
        with self.assertRaisesRegex(f.FourYearEvidenceError,"out_of_budget"):
            f.plan(m,c,b,limit=40)
        with self.assertRaisesRegex(f.FourYearEvidenceError,"incomplete_pilot"):
            f.merge(m,c,b,[reports("005930")],limit=2)
        with self.assertRaisesRegex(f.FourYearEvidenceError,"duplicate_pilot"):
            f.merge(m,c,b,[reports("005930"),reports("005930")],limit=2)
        altered=copy.deepcopy(b);altered["asof"]="2026-10-20"
        with self.assertRaisesRegex(f.FourYearEvidenceError,"incompatible_annual"):
            f.plan(m,c,altered)
        unknown=copy.deepcopy(b);unknown["records"][0]["code"]="999999"
        with self.assertRaisesRegex(f.FourYearEvidenceError,"untrusted"):
            f.plan(m,c,unknown)

    def test_atomic_write_single_manual_run_and_prior4y_exclusion(self):
        m,c,b=inputs()
        calls=[]
        def collector(stocks,day,key,years,budget_seconds):
            calls.append((len(stocks),day,years,budget_seconds))
            return [reports(r["code"],scope="CFS") for r in stocks]
        with tempfile.TemporaryDirectory() as tmp:
            dst=Path(tmp)/"four-year.json"
            ref=f.write(m,c,b,output=dst,key="redacted-secret",collector=collector,limit=1)
            self.assertEqual(calls,[(1,DAY,4,420)])
            self.assertEqual(ref["four_consecutive_years_observed_count"],1)
            self.assertNotIn("redacted-secret",dst.read_text())
            self.assertNotIn("corpCode.xml",dst.read_text())
            saved=dst.read_bytes()
            with self.assertRaisesRegex(f.FourYearEvidenceError,"incomplete_pilot"):
                f.write(m,c,b,output=dst,key="redacted-secret",
                        collector=lambda *a,**k:[],limit=1)
            self.assertEqual(saved,dst.read_bytes())
            with self.assertRaisesRegex(f.FourYearEvidenceError,"missing_manual_pilot_key"):
                f.write(m,c,b,output=dst,collector=collector,limit=1)
            prior=json.loads(dst.read_text(encoding="utf8"))
            requested=f.plan(m,c,b,prior=prior,limit=1)[3]
            self.assertEqual(requested,["247540"])
            final=f.merge(m,c,b,[reports("247540",scope="OFS")],prior=prior,limit=1)
            self.assertEqual(final["four_consecutive_years_observed_count"],2)
            self.assertTrue(all(r["v3_full100_investable_score"] is None
                                for r in final["records"]))


if __name__=="__main__":
    unittest.main()
