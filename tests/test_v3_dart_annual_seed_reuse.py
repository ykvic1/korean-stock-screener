"""No-network checks that real same-day DART annual evidence saves redundant
latest-FY queries without minting 4-year completeness or PIT authentication.
"""
import copy
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
from zipfile import ZipFile

import official_data
import v3_dart_four_year_reference as four
from test_v3_dart_four_year_reference import inputs, reports


def annual_seed(code="005930",year=2025):
    row=reports(code)["reports"][-1]
    row["year"]=year
    row["receipt_id"]=f"{year+1}0318001162"
    return row


class FakeMappingResponse:
    def __init__(self):
        out=BytesIO()
        with ZipFile(out,"w") as z:
            z.writestr("CORPCODE.xml",
                "<result><list><stock_code>005930</stock_code>"
                "<corp_code>00126380</corp_code></list></result>")
        self.content=out.getvalue()
    def raise_for_status(self):
        return None


class FakeMappingSession:
    def __init__(self):
        self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append(url)
        return FakeMappingResponse()


class SeedReuseTests(unittest.TestCase):
    def test_selected_year_latest_report_requires_real_complete_money_accounts(self):
        m,c,book=inputs()
        rows=book["records"]
        samsung=next(r for r in rows if r["code"]=="005930")
        samsung["annual"]["values"]["liabilities"]=20_000_000_000
        samsung["annual"]["values"]["owners_equity"]=25_000_000_000
        targets=[{"code":"005930"},{"code":"247540"}]
        seeds=four.latest_verified_annual_seeds(book,targets,m["date"])
        self.assertEqual(set(seeds),{"005930"})
        self.assertEqual(seeds["005930"]["year"],2025)
        self.assertFalse(four._record("005930",[seeds["005930"]],m["date"])["same_scope_four_years"])
        for change in ("bad_receipt","missing_ocf","old_year","future_receipt"):
            invalid=copy.deepcopy(book)
            row=next(r for r in invalid["records"] if r["code"]=="005930")
            annual=row["annual"]
            if change=="bad_receipt":
                annual["receipt_id"]="not_a_receipt"
            elif change=="missing_ocf":
                annual["values"]["operating_cashflow"]=None
            elif change=="old_year":
                annual["year"]=2024
            else:
                annual["receipt_id"]="20271020001162"
            self.assertNotIn("005930",
                four.latest_verified_annual_seeds(invalid,targets,m["date"]))
        with self.assertRaisesRegex(four.FourYearEvidenceError,"date_mismatch"):
            four.latest_verified_annual_seeds(book,targets,"2026-10-18")

    def test_real_4year_collector_skips_one_already_observed_fiscal_year(self):
        session=FakeMappingSession()
        years=[]
        def fake_dart(sess,endpoint,key,**kw):
            years.append(int(kw["bsns_year"]))
            return [{"rcept_no":f"{int(kw['bsns_year'])+1}0318001162"}]
        observed={"values":{
            "revenue":100_000_000_000,
            "operating_income":10_000_000_000,
            "operating_cashflow":11_000_000_000,
            "liabilities":20_000_000_000,
            "equity":30_000_000_000,
            "owners_equity":25_000_000_000,
            "owners_profit":3_000_000_000}}
        seed=annual_seed()
        with patch("requests.Session",return_value=session),patch.object(
                official_data,"dart_json",side_effect=fake_dart),patch(
                "financial_metrics.metrics",return_value=observed),patch.object(
                official_data.time,"sleep",return_value=None):
            rows=official_data.dart_collect([{"code":"005930","market_cap_eok":7000}],
                "2026-10-19","redacted-secret",years=4,budget_seconds=420,
                annual_seed_by_code={"005930":seed})
        self.assertEqual(years,[2024,2023,2022])
        self.assertEqual(len(session.calls),1)  # company mapping once
        self.assertEqual(rows[0]["status"],"observed")
        self.assertEqual(len(rows[0]["reports"]),4)
        self.assertEqual(rows[0]["newly_fetched_annual_years"],3)
        self.assertTrue(rows[0]["latest_year_reused_from_prior_dart"])
        self.assertEqual({r["year"] for r in rows[0]["reports"]},
                         {2022,2023,2024,2025})
        self.assertEqual(rows[0]["reports"][0]["receipt_id"],seed["receipt_id"])
        result=four._record("005930",rows[0]["reports"],"2026-10-19")
        self.assertTrue(result["core_financial_observed_for_research"])
        self.assertIsNone(result["financial15_confirmed_points"])
        self.assertFalse(result["four_year_financial_pit_authenticated"])

    def test_bad_seed_fails_before_any_official_api_call(self):
        seed=annual_seed()
        cases=[{"000040":seed},{"005930":dict(seed,year=2024)},
               {"005930":dict(seed,receipt_id="20271019001162")},
               {"005930":dict(seed,values={**seed["values"],"revenue":None})}]
        with patch("requests.Session") as provider:
            for index,case in enumerate(cases):
                with self.subTest(index=index):
                    with self.assertRaises(ValueError):
                        official_data.dart_collect([{"code":"005930"}],"2026-10-19",
                            "redacted",years=4,budget_seconds=420,
                            annual_seed_by_code=case)
            with self.assertRaisesRegex(ValueError,"invalid_annual_seed_scope"):
                official_data.dart_collect([{"code":"005930"}],"2026-10-19",
                    "redacted",years=1,budget_seconds=300,
                    annual_seed_by_code={"005930":seed})
            provider.assert_not_called()

    def test_default_live_write_passes_only_proven_seed_and_mock_contract_unchanged(self):
        m,c,book=inputs()
        row=next(r for r in book["records"] if r["code"]=="005930")
        row["annual"]["values"]["liabilities"]=22_000_000_000
        # CFS has observed owners' equity; OFS fixture intentionally lacks it.
        seen=[]
        def fake_live(stocks,day,key,years,budget_seconds,annual_seed_by_code):
            seen.append((len(stocks),years,set(annual_seed_by_code)))
            return [reports(s["code"],scope="CFS" if s["code"]=="005930" else "OFS")
                    for s in stocks]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(official_data,"dart_collect",side_effect=fake_live):
                outcome=four.write(m,c,book,output=Path(folder)/"four.json",
                                   key="redacted",limit=2)
            self.assertEqual(seen,[(2,4,{"005930"})])
            self.assertEqual(outcome["same_day_valid_annual_report_reused"],1)
            self.assertEqual(outcome["four_consecutive_years_observed_count"],2)
            self.assertFalse(outcome["full_v3_ready"])
            # Injected (offline / manual) collectors preserve old signatures.
            called=[]
            def mock(stocks,day,key,years,budget_seconds):
                called.append(len(stocks))
                return [reports(s["code"]) for s in stocks]
            with patch.object(official_data,"dart_collect") as unused:
                next_out=four.write(m,c,book,output=Path(folder)/"mock.json",
                                    key="redacted",collector=mock,limit=1)
            self.assertEqual(called,[1])
            self.assertEqual(next_out["same_day_valid_annual_report_reused"],0)
            unused.assert_not_called()


    def test_cached_latest_filing_does_not_fake_provider_health(self):
        m,c,book=inputs()
        first=reports("005930")
        second=reports("247540")
        for item in (first,second):
            item["newly_fetched_annual_years"]=0
            item["latest_year_reused_from_prior_dart"]=True
        merged=four.merge(m,c,book,[first,second],limit=2)
        self.assertEqual(merged["observed_filings_issuers_this_run"],2)
        self.assertEqual(merged["newly_fetched_filings_issuers_this_run"],0)
        self.assertEqual(merged["consecutive_healthy_batches"],0)
        self.assertEqual(merged["next_schedule_max_issuers"],4)
        first["newly_fetched_annual_years"]=3
        second["newly_fetched_annual_years"]=3
        healthy=four.merge(m,c,book,[first,second],limit=2)
        self.assertEqual(healthy["newly_fetched_filings_issuers_this_run"],2)
        self.assertEqual(healthy["consecutive_healthy_batches"],1)
        first["newly_fetched_annual_years"]=5
        with self.assertRaisesRegex(four.FourYearEvidenceError,"invalid_fresh"):
            four.merge(m,c,book,[first,second],limit=2)


if __name__=="__main__":
    unittest.main()
