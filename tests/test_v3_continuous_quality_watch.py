"""Continuous six-domain quality watcher: source guard, stable reporting, 4y PIT."""
import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from test_v3_six_pillar_source_readiness import fixture
import v3_continuous_quality_watch as q
from v3_dart_four_year_reference import _record, SCHEMA


def valid_four(m, c):
    day=m["date"]
    reports=[
        {"year":yr,"fs_div":"CFS","currency":"KRW",
         "receipt_id":str(yr+1)+"0318001162",
         "values":{"revenue":100000*(yr-2020),
                   "operating_income":1000,"operating_cashflow":3000,
                   "liabilities":10000,"equity":20000,
                   "owners_equity":19000,"owners_profit":500}}
        for yr in (2022,2023,2024,2025)
    ]
    # Use a more recent synthetic market day: the original 2026-10-19 works.
    row=_record("000001",reports,day)
    return {"schema":SCHEMA,"asof":day,"listed_total":c["listed_total"],
            "records":[row],"pilot_issuer_count":1,
            "four_consecutive_years_observed_count":1,
            "four_year_income_values_observed_count":1,
            "last_three_years_operating_cashflow_observed_count":1,
            "latest_year_balance_values_observed_count":1,
            "four_year_core_financial_research_observed_count":1,
            "full_v3_ready":False,"four_years_all_financial_pillars_certified":False,
            "raw_account_rows_stored":False,"confirmed_six_pillar_ranks":None}


class QualityWatchUnitTests(unittest.TestCase):
    def test_legacy_previous_day_warns_not_falsely_marks_no_technical(self):
        m,c,b=fixture()
        m["stocks"][0]["v3_pattern_integrated"] = None
        v=q.assess_sources(m,c,b,today=date(2026,10,20))
        self.assertEqual(v["market_data_calendar_age_days"],1)
        self.assertNotIn("new_market_snapshot_missing_all_technical25_observations",
                         v["source_quality_issues"])
        self.assertEqual(v["status"],"attention")
        self.assertEqual(v["contract_weights"],
            {"ias":20,"technical":25,"financial":15,"valuation":15,
             "industry":15,"momentum":10})
        self.assertIsNone(v["full_investment_rank_count"])
        self.assertIsNone(v["trade_recommendation"])
        self.assertFalse(v["full_investment_rank_certified"])

    def test_recent_snapshot_missing_new_technical_hook_is_red(self):
        m,c,b=fixture()
        m["date"]=c["asof"]=b["asof"]="2026-10-20"
        m["stocks"][0]["v3_pattern_integrated"]=None
        # Inflate to real 100+ screened records; do not use tiny unit sample
        # to trigger a full-market publication-health failure.
        m["stocks"]=[{"code":str(i).zfill(6),"flows_confirmed":False,
                      "pattern_research":None,"pattern_weekly_research":None,
                      "v3_pattern_integrated":None} for i in range(100)]
        m["candidate_count"]=100
        m["flow_confirmed_count"]=0
        c["rows"]=[{"code":str(i).zfill(6)} for i in range(100)]
        c["listed_total"]=b["market_listed_total"]=100
        b["attempted_reference_count"]=b["observed_annual_count"]=1
        b["pbr_simple_reference_count"]=1
        z=q.assess_sources(m,c,b,today=date(2026,10,20))
        self.assertEqual(z["status"],"critical")
        self.assertIn("new_market_snapshot_missing_all_technical25_observations",
                      z["source_quality_issues"])
        self.assertIn("completed_weekly_pattern_research_absent",
                      z["source_quality_warnings"])

    def test_holiday_interval_no_false_exchange_open_claim(self):
        m,c,b=fixture()
        x=q.assess_sources(m,c,b,today=date(2026,10,25))
        self.assertEqual(x["status"],"attention")
        self.assertIn("market_snapshot_older_than_four_days_check_calendar_or_batch",
                      x["source_quality_warnings"])
        self.assertFalse(x["market_trading_day_verified"])
        with self.assertRaisesRegex(ValueError,"future_market_snapshot"):
            q.assess_sources(m,c,b,today=date(2026,10,18))

    def test_extreme_age_and_bad_price_coverage_are_critical(self):
        m,c,b=fixture()
        x=q.assess_sources(m,c,b,today=date(2026,11,5))
        self.assertIn("published_snapshot_at_least_14_calendar_days_old",
                      x["source_quality_issues"])
        m["stocks"]=m["stocks"][:1]
        m["candidate_count"]=1
        m["flow_confirmed_count"]=1
        c["rows"]=[{"code":str(i).zfill(6)} for i in range(100)]
        c["listed_total"]=b["market_listed_total"]=100
        y=q.assess_sources(m,c,b,today=date(2026,10,19))
        self.assertIn("price_screened_coverage_below_80_percent",
                      y["source_quality_issues"])

    def test_valid_optional_four_year_is_not_a_financial15(self):
        m,c,b=fixture()
        four=valid_four(m,c)
        x=q.assess_sources(m,c,b,four,today=date(2026,10,20))
        self.assertEqual(x["dart_four_year"]["four_year_observed"],1)
        self.assertEqual(
            x["dart_four_year"]["four_year_core_financial_research_observed"],1)
        self.assertEqual(x["dart_four_year"]["pilot_issuer_count"],1)
        self.assertFalse(x["full_investment_rank_certified"])
        self.assertFalse(x["published_data_modified"])

    def test_forged_four_year_flags_future_receipts_and_repeated_issuers_fail(self):
        m,c,b=fixture()
        correct=valid_four(m,c)
        tampered=[
            lambda a:a.update({"four_consecutive_years_observed_count":3}),
            lambda a:a.update({"four_year_core_financial_research_observed_count":3}),
            lambda a:a["records"][0].update(
                {"core_financial_observed_for_research":False}),
            lambda a:a["records"][0].update({"financial15_confirmed_points":15}),
            lambda a:a["records"][0].update({"annual_revenue_cagr3_reference_pct":999}),
            lambda a:a["records"][0]["reports"][0].update({"receipt_id":"20300318001162"}),
            lambda a:a.update({"asof":"2026-10-20"}),
            lambda a:a["records"].append(copy.deepcopy(a["records"][0])),
            lambda a:a.update({"full_v3_ready":True}),
        ]
        for idx,break_it in enumerate(tampered):
            with self.subTest(idx=idx):
                four=copy.deepcopy(correct)
                break_it(four)
                with self.assertRaises(ValueError):
                    q.assess_sources(m,c,b,four,today=date(2026,10,20))

    def test_cli_failure_still_writes_diagnostic_artifact(self):
        m,c,b=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            data=[]
            for n,v in (("market",m),("catalog",c),("book",b)):
                path=root/(n+".json")
                path.write_text(json.dumps(v),encoding="utf8")
                data.append(path)
            dest=root/"report.json"
            summary=root/"summary.md"
            with patch.object(q,"assess_sources",side_effect=
                              q.ReadinessError("synthetic_mismatch")):
                rc=q.main(["--market",str(data[0]),"--catalog",str(data[1]),
                           "--book",str(data[2]),"--four-year",str(root/"none.json"),
                           "--output",str(dest),"--summary",str(summary)])
            self.assertEqual(rc,2)
            report=json.loads(dest.read_text())
            self.assertEqual(report["source_quality_issues"],["synthetic_mismatch"])
            self.assertIn("V3 데이터 지속 품질감사",summary.read_text())

    def test_workflow_recurs_without_changing_trading_providers(self):
        path=Path(".github/workflows/v3-six-pillar-source-readiness.yml")
        wf=path.read_text(encoding="utf8")
        self.assertIn("cron: '0 22 * * 1-5'",wf)
        self.assertIn("workflow_run:",wf)
        self.assertIn("permissions:\n  contents: read",wf)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",wf)
        self.assertIn("v3_continuous_quality_watch.py",wf)
        self.assertNotIn("DART_API_KEY",wf)
        self.assertNotIn("KRX_API_KEY",wf)
        self.assertNotIn("git push",wf)


if __name__=="__main__":
    unittest.main()
