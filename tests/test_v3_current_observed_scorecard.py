"""Strict full-listed same-day scorecard regression; no new provider calls."""
import copy
import csv
from datetime import date, timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from test_v3_dart_rolling_book_reference import fixture
from v3_all_listed_scorecard import FullMarketScorecardError
from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build
from v3_dart_bulk_history import research_parts
from v3_current_observed_scorecard import (
    generate,to_csv,main,finance_source,cashflow_source,LivePartialScorecardError,
)

DOCS=Path(__file__).resolve().parents[1]/"docs"

def small():
    market,catalog=fixture()
    for stock in market["stocks"]:
        entry=next(r for r in catalog["rows"] if r["code"]==stock["code"])
        entry["name"]=stock["name"]
    market["stocks"][0].update({
        "volume_ratio":1.5,"obv_rising":True,
        "price60":2.0,"divergence":True,
    })
    return market,catalog


class CurrentAllListedScorecardTests(unittest.TestCase):
    def test_only_true_observations_are_scored_and_missing_is_not_zero(self):
        market,catalog=small()
        volume=volume_build(market,catalog)
        price=price_build(market,catalog)
        rows,summary=generate(market,catalog,volume,price)
        by={r["code"]:r for r in rows}
        self.assertEqual(summary["listed_total"],5)
        self.assertEqual(summary["area_budgets"], {"volume":20,"technical":25,"financial":20,"valuation":20,"momentum":15})
        self.assertNotIn("industry",summary["area_budgets"])
        self.assertEqual(sum(summary["area_budgets"].values()),100)
        self.assertTrue(all("industry" not in r["factors"] and
                            "industry" not in r["factor_available"] for r in rows))
        self.assertEqual(summary["provider_requests"],0)
        self.assertEqual(sum(summary["factor_observed_weight_ceiling"].values())+summary["total_unobserved_weight_gap"],100)
        self.assertEqual(sum(summary["factor_unobserved_weight_gap"].values()),summary["total_unobserved_weight_gap"])
        self.assertEqual(set(summary["factor_unobserved_weight_gap"]),set(summary["area_budgets"]))
        self.assertEqual(sum(summary["available_weight_distribution"].values()),5)
        self.assertEqual(summary["available_weight_distribution"].get("0",0), sum(r["available"]==0 for r in rows))
        self.assertEqual(summary["five_area_observed_overlap_issuers"],0)
        self.assertEqual(summary["max_single_issuer_available_weight"],max(r["available"] for r in rows))
        self.assertEqual(sum(sum(d.values()) for d in summary["available_weight_distribution_by_market"].values()),5)
        self.assertEqual(sum(summary["five_area_observed_overlap_by_market"].values()),0)
        self.assertEqual(set(summary["available_weight_distribution_by_market"]),set(r["market"] for r in rows))
        self.assertEqual(summary["observed_issuer_counts"]["volume"],1)
        # V3.0b: continuous same-day missingness census for all five areas.
        self.assertTrue(summary["unacquired_weight_is_not_a_zero_score"])
        self.assertEqual(set(summary["area_available_weight_distribution"]),
                         set(summary["area_budgets"]))
        for area,budget in summary["area_budgets"].items():
            weights=[r["factor_available"][area] for r in rows]
            distribution=summary["area_available_weight_distribution"][area]
            self.assertEqual(sum(distribution.values()),len(rows))
            self.assertEqual(
                {str(w):weights.count(w) for w in set(weights)},distribution)
            self.assertEqual(summary["unacquired_area_issuer_counts"][area],
                             sum(w==0 for w in weights))
            self.assertEqual(summary["partial_area_issuer_counts"][area],
                             sum(0<w<budget for w in weights))
            self.assertEqual(summary["complete_area_issuer_counts"][area],
                             sum(w==budget for w in weights))
            self.assertEqual(summary["unacquired_weight_issuer_slots"][area],
                             sum(budget-w for w in weights))
            self.assertEqual(len(rows),sum((
                summary["unacquired_area_issuer_counts"][area],
                summary["partial_area_issuer_counts"][area],
                summary["complete_area_issuer_counts"][area])))
        self.assertEqual(summary["total_unacquired_weight_issuer_slots"],
                         sum(r["unavailable"] for r in rows))
        self.assertEqual(summary["certified_100point_count"],0)
        self.assertEqual(summary["highest_currently_observable_weight"],sum(max(r["factor_available"][a] for r in rows) for a in summary["area_budgets"]))
        self.assertEqual(by["005930"]["factors"]["volume"],16.0)
        self.assertEqual(by["005930"]["factor_available"]["volume"],16)
        self.assertEqual(by["300004"]["earned"],None)
        self.assertEqual(by["300004"]["available"],0)
        self.assertEqual(by["300004"]["unavailable"],100)
        self.assertEqual(by["005930"]["unavailable"],84)
        txt=to_csv(rows,summary)
        self.assertTrue(txt.startswith("\ufeff"))
        csv_rows=list(csv.DictReader(StringIO(txt.lstrip("\ufeff"))))
        self.assertEqual(len(csv_rows),5)
        self.assertTrue(all("산업_잠정점수" not in r for r in csv_rows))
        self.assertNotIn("industry",summary["observed_issuer_counts"])
        self.assertEqual(next(r for r in csv_rows if r["종목코드"]=="300004")
                         ["관측잠정합계_100점중"],"")
        self.assertEqual(next(r for r in csv_rows if r["종목코드"]=="300004")
                         ["미확보배점_100점중"],"100")
        self.assertTrue(all(r["정식투자점수"]=="" for r in csv_rows))

    def test_date_tamper_or_amount_inflation_is_not_accepted(self):
        market,catalog=small()
        v=volume_build(market,catalog)
        p=price_build(market,catalog)
        bad=copy.deepcopy(v)
        bad["records"]["005930"]["observed_points"]=20
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,bad,p)
        bad=copy.deepcopy(p)
        bad["asof"]=(date.fromisoformat(market["date"])+timedelta(days=1)).isoformat()
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,v,bad)
        bad=copy.deepcopy(p)
        bad["certified_100point_investment_issuers"]=3
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,v,bad)

    def test_cli_write_then_verify_then_tamper_csv_rejected(self):
        market,catalog=small()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            sources={
                "data.json":market,
                "v22-market-catalog.json":catalog,
                "v3-volume-obv-observed.json":volume_build(market,catalog),
                "v3-current-price-technical-momentum.json":price_build(market,catalog),
            }
            for name,value in sources.items():
                (root/name).write_text(json.dumps(value),encoding="utf8")
            main(["--docs",str(root),"--mode","write"])
            main(["--docs",str(root),"--mode","verify"])
            report=root/"v3-current-observed-all-listed-summary.json"
            clean_report=report.read_text(encoding="utf8")
            forged=json.loads(clean_report)
            forged["unacquired_area_issuer_counts"]["volume"]=0
            report.write_text(json.dumps(forged),encoding="utf8")
            with self.assertRaises(LivePartialScorecardError):
                main(["--docs",str(root),"--mode","verify"])
            report.write_text(clean_report,encoding="utf8")
            main(["--docs",str(root),"--mode","verify"])
            p=root/"v3-current-observed-all-listed.csv"
            clean=p.read_text(encoding="utf8")
            p.write_text(clean.replace("16.0","20.0"),encoding="utf8")
            with self.assertRaises(LivePartialScorecardError):
                main(["--docs",str(root),"--mode","verify"])

    def test_momentum_fifteen_budget_keeps_unobserved_seven_blank(self):
        market,catalog=small()
        price=price_build(market,catalog)
        for record in price["rows"].values():
            ref=record.get("momentum_research_reference")
            if ref is not None:
                self.assertEqual(ref["momentum_maximum"],15)
                self.assertIsNone(ref["momentum_parts"]["positive_days20"])
                self.assertIsNone(ref["momentum_parts"]["trend_persistence60"])
                self.assertIsNone(ref["momentum_parts"]["reversal_breadth120"])
        rows,report=generate(market,catalog,volume_build(market,catalog),price)
        self.assertEqual(len(rows),report["listed_total"])

    def test_refresh_derived_recomputes_stale_saved_products_without_false_score(self):
        market,catalog=small()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            inputs={
                "data.json":market,
                "v22-market-catalog.json":catalog,
                "v3-volume-obv-observed.json":{"stale":True},
                "v3-current-price-technical-momentum.json":{"stale":True},
            }
            for name,value in inputs.items():
                (root/name).write_text(json.dumps(value),encoding="utf8")
            with self.assertRaises(LivePartialScorecardError):
                main(["--docs",str(root),"--mode","write"])
            main(["--docs",str(root),"--mode","write","--refresh-derived"])
            main(["--docs",str(root),"--mode","verify","--refresh-derived"])
            with self.assertRaises(LivePartialScorecardError):
                main(["--docs",str(root),"--mode","verify"])

    def test_current_coverage_publisher_and_homepage_link(self):
        base=Path(__file__).resolve().parents[1]
        flow=(base/".github/workflows/v3-current-observed-scorecard.yml").read_text(encoding="utf8")
        self.assertIn("v3_current_observed_scorecard.py --mode write",flow)
        self.assertIn("v3_current_observed_scorecard.py --mode verify",flow)
        self.assertIn("docs/v3-dart-bulk-provisional.json",flow)
        self.assertIn("docs/v3-real-source-six-area-coverage.json",flow)
        self.assertIn("docs/v3-dart-cashflow-reference.json",flow)
        self.assertIn("docs/v3-dart-fcf-research-preview.json",flow)
        self.assertIn("group: korea-stock-screener-current-observed-publisher",flow)
        self.assertNotIn("DART_API_KEY:",flow)
        self.assertNotIn("KRX_API_KEY:",flow)
        self.assertNotIn("git push --force",flow)
        add=[line for line in flow.splitlines() if line.strip().startswith("git add docs/")]
        self.assertGreaterEqual(len(add),2)
        self.assertTrue(all(
            line.strip()=="git add docs/v3-current-observed-all-listed.csv docs/v3-current-observed-all-listed-summary.json"
            for line in add))
        for html in ("index.html","workspace.html"):
            text=(base/"docs"/html).read_text(encoding="utf8")
            self.assertIn('href="v3-current-observed-all-listed.csv"',text)
            self.assertIn('당일 실측 잠정점수 CSV',text)
            self.assertNotIn('당일 3영역 잠정점수 CSV',text)
            self.assertNotIn('href="v3-all-listed-scores.csv"',text)

    def test_financial_partial_join_preserves_zero_and_missing_separately(self):
        market,catalog=small()
        volume=volume_build(market,catalog)
        price=price_build(market,catalog)
        example={
            "code":"005930","market":"KOSPI","asof":market["date"],
            "financial_candidate_points":0.0,
            "financial_available_max":8,
            "valuation_candidate_points":2.5,
            "valuation_available_max":5,
            "confirmed_v3_score":None,"scoring_certified":False,
        }
        rows,summary=generate(market,catalog,volume,price,
                              financial={"005930":example})
        r=next(q for q in rows if q["code"]=="005930")
        self.assertEqual(r["factors"]["financial"],0)
        self.assertEqual(r["factor_available"]["financial"],8)
        self.assertEqual(r["factors"]["valuation"],2.5)
        self.assertEqual(r["factor_available"]["valuation"],5)
        self.assertEqual(summary["highest_currently_observable_weight"],sum(max(r["factor_available"][a] for r in rows) for a in summary["area_budgets"]))
        self.assertEqual(summary["observed_issuer_counts"]["financial"],1)
        self.assertEqual(summary["observed_issuer_counts"]["valuation"],1)
        csv_rows=list(csv.DictReader(StringIO(
            to_csv(rows,summary).lstrip("\ufeff"))))
        match=next(q for q in csv_rows if q["종목코드"]=="005930")
        self.assertEqual(match["재무_잠정점수"],"0.0")
        self.assertEqual(match["밸류에이션_잠정점수"],"2.5")
        self.assertEqual(match["정식투자점수"],"")
        bad=copy.deepcopy(example)
        bad["scoring_certified"]=True
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,volume,price,financial={"005930":bad})
        bad=copy.deepcopy(example)
        bad["asof"]="1900-01-01"
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,volume,price,financial={"005930":bad})

    def test_subfactor_coverage_shows_real_zero_and_unknown_separately(self):
        market,catalog=small()
        v=volume_build(market,catalog)
        p=price_build(market,catalog)
        _,unknown=generate(market,catalog,v,p)
        missing=unknown["financial_valuation_component_observed_issuers"]
        self.assertTrue(all(x is None for x in missing.values()))
        self.assertTrue(unknown["subfactor_missing_is_not_a_zero_score"])
        self.assertEqual(unknown["financial_valuation_component_observation_status"][
            "fiscal"],"unavailable_or_legacy_fiscal_component_contract")
        weights={"revenue_cagr_3y_pct":4,"operating_margin_3y_pct":4,
                 "liabilities_to_assets_pct":4,"operating_roa_3y_pct":4,
                 "pbr":5,"earnings_yield_pct":5,"sales_to_market_cap":5}
        components={key:{"max_points":0,"candidate_points":None}
                    for key in weights}
        components["revenue_cagr_3y_pct"]={"max_points":4,
                                           "candidate_points":0.0}
        components["pbr"]={"max_points":5,"candidate_points":0.0}
        fiscal={"code":"005930","market":"KOSPI","asof":market["date"],
                "financial_candidate_points":0.0,"financial_available_max":4,
                "valuation_candidate_points":0.0,"valuation_available_max":5,
                "unverified_components":components,
                "confirmed_v3_score":None,"scoring_certified":False}
        rows,summary=generate(market,catalog,v,p,financial={"005930":fiscal})
        self.assertEqual(summary["financial_valuation_component_observed_issuers"][
            "revenue_cagr_3y_pct"],1)
        self.assertEqual(summary["financial_valuation_component_observed_issuers"][
            "pbr"],1)
        self.assertEqual(summary["financial_valuation_component_observed_issuers"][
            "operating_roa_3y_pct"],0)
        self.assertEqual(summary["financial_valuation_component_unobserved_issuers"][
            "revenue_cagr_3y_pct"],4)
        self.assertIsNone(summary["financial_valuation_component_observed_issuers"][
            "financial_fcf_assets_3y"])
        self.assertEqual(summary["financial_valuation_component_observation_status"][
            "fiscal"],"same_day_replayed_component_receipts")
        scored=next(x for x in rows if x["code"]=="005930")
        self.assertEqual(scored["factors"]["financial"],0)
        self.assertEqual(scored["factor_available"]["financial"],4)
        self.assertIsNone(scored["official_score"])
        fake=copy.deepcopy(fiscal)
        fake["unverified_components"]["pbr"]["candidate_points"]=None
        with self.assertRaisesRegex(
            LivePartialScorecardError,"untrusted_dart_subfactor_observation"):
            generate(market,catalog,v,p,financial={"005930":fake})

    def test_fcf_receipt_candidate_join_no_double_count_or_false_certification(self):
        market,catalog=small()
        v=volume_build(market,catalog)
        p=price_build(market,catalog)
        base={"code":"005930","market":"KOSPI","asof":market["date"],
              "financial_candidate_points":3.0,
              "financial_available_max":8,
              "valuation_candidate_points":4.0,
              "valuation_available_max":10,
              "confirmed_v3_score":None,"scoring_certified":False}
        item={"code":"005930","market":"KOSPI","asof":market["date"],
              "financial_fcf_assets_candidate_points":0.0,
              "financial_fcf_assets_available_max":4,
              "valuation_fcf_yield_candidate_points":5.0,
              "valuation_fcf_yield_available_max":5,
              "strict_receipt_scope_matched":True,
              "historical_PIT_certified":False,
              "scoring_certified":False}
        rows,summary=generate(market,catalog,v,p,
                 financial={"005930":base},cashflow={"005930":item})
        row=next(x for x in rows if x["code"]=="005930")
        self.assertEqual(row["factors"]["financial"],3.0)
        self.assertEqual(row["factor_available"]["financial"],12)
        self.assertEqual(row["factors"]["valuation"],9.0)
        self.assertEqual(row["factor_available"]["valuation"],15)
        self.assertEqual(row["fcf_subpoints"]["financial4"],0.0)
        self.assertEqual(row["fcf_subpoints"]["valuation5"],5.0)
        self.assertEqual(summary["highest_currently_observable_weight"],sum(max(r["factor_available"][a] for r in rows) for a in summary["area_budgets"]))
        self.assertEqual(summary["fcf_observed_issuer_counts"],
                         {"financial4":1,"valuation5":1})
        view=list(csv.DictReader(StringIO(to_csv(rows,summary).lstrip("\ufeff"))))
        got=next(x for x in view if x["종목코드"]=="005930")
        self.assertEqual(got["FCF자산3년_잠정점수"],"0.0")
        self.assertEqual(got["FCF수익률_잠정점수"],"5.0")
        self.assertEqual(got["재무_산출배점"],"12")
        self.assertEqual(got["가치_산출배점"],"15")
        self.assertEqual(got["정식투자점수"],"")
        none=next(x for x in view if x["종목코드"]=="300004")
        self.assertEqual(none["FCF자산3년_잠정점수"],"")
        self.assertEqual(none["미확보배점_100점중"],"100")
        bad=copy.deepcopy(item)
        bad["strict_receipt_scope_matched"]=False
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,v,p,financial={"005930":base},
                     cashflow={"005930":bad})
        bad=copy.deepcopy(item)
        bad["financial_fcf_assets_candidate_points"]=4.5
        with self.assertRaises(LivePartialScorecardError):
            generate(market,catalog,v,p,financial={"005930":base},
                     cashflow={"005930":bad})
        bad=copy.deepcopy(base)
        bad["financial_available_max"]=21
        with self.assertRaises((LivePartialScorecardError, FullMarketScorecardError)):
            generate(market,catalog,v,p,financial={"005930":bad},
                     cashflow={"005930":item})

    def test_real_stored_three_year_receipts_replay_before_publish(self):
        docs=DOCS
        names=("data.json","v22-market-catalog.json",
               "v3-dart-bulk-history.json","v3-dart-cashflow-reference.json",
               "v3-dart-fcf-research-preview.json")
        market,catalog,history,cash,preview=[
           json.loads((docs/x).read_text(encoding="utf8")) for x in names]
        if any(x["asof"]!=catalog["date"] for x in (history,cash,preview)):
            self.skipTest("real_FCF_sources_not_same_asof")
        actual=cashflow_source(market,catalog,history,cash,preview)
        self.assertIsNotNone(actual)
        rows,source=actual
        self.assertEqual(sum(r["financial_fcf_assets_available_max"]==4
                             for r in rows.values()),
                         source["financial_fcf4_research_candidate_issuer_count"])
        self.assertEqual(sum(r["valuation_fcf_yield_available_max"]==5
                             for r in rows.values()),
                         source["valuation_fcf5_research_candidate_issuer_count"])
        self.assertGreater(len(rows),0)
        self.assertEqual(source["hist_PIT_verified"],False)
        self.assertIsNone(source["six_pillar_confirmed_100pt"])
        bad=copy.deepcopy(preview)
        bad["buy_recommendations_authorized"]=True
        with self.assertRaises(LivePartialScorecardError):
            cashflow_source(market,catalog,history,cash,bad)
        # Use the fully recomputed CURRENT receipt-matched source. The
        # previously saved preview may legitimately predate unrelated new
        # major-company receipts and has different valid source hashes.
        bad=copy.deepcopy(source)
        if bad["rows"]:
            bad["rows"][0]["financial_fcf_assets_candidate_points"]=99
            with self.assertRaises(LivePartialScorecardError):
                cashflow_source(market,catalog,history,cash,bad)
        old=copy.deepcopy(cash)
        old["asof"]="2000-01-01"
        self.assertIsNone(cashflow_source(market,catalog,history,old,preview))

    def test_actual_saved_same_day_dart_replays_only_if_audited(self):
        root=DOCS
        names=("v22-market-catalog.json","v3-dart-bulk-history.json",
               "v3-dart-bulk-provisional.json","v3-dart-book-reference.json",
               "v3-real-source-six-area-coverage.json")
        catalog,history,parts,annual,audit=[
            json.loads((root/name).read_text(encoding="utf8"))
            for name in names]
        if (history["asof"]!=catalog["date"] or
            annual["asof"]!=catalog["date"] or
            audit["market_asof"]!=catalog["date"]):
            self.skipTest("official_filing_collection_not_yet_reconciled_with_market")
        aligned=finance_source(catalog,history,parts,annual,audit)
        source_replay_matches=any(
            research_parts(history,catalog,annual,**mode)==parts
            for mode in ({}, {"include_balance_factors":False},
                         {"include_balance_factors":False,
                          "include_sales_to_cap":False}))
        # A newer annual book may be published after the same-close source
        # report. This is an expected source-only replay wait, never a
        # reason to trust a partial report against different filing data.
        if source_replay_matches:
            self.assertIsNotNone(aligned,"exact same-day DART source must replay")
            self.assertEqual(sum(r["financial_available_max"]>0
                                 for r in aligned.values()),
                             audit["six_pillar_numeric_provisional_issuer_counts"]["financial"])
            self.assertEqual(sum(r["valuation_available_max"]>0
                                 for r in aligned.values()),
                             audit["six_pillar_numeric_provisional_issuer_counts"]["valuation"])
        else:
            self.assertIsNone(aligned,"newer annual source must await replay")
        fake=copy.deepcopy(audit)
        fake["source_sha256_canonical"]["dart_four_year_archive"]="0"*64
        self.assertIsNone(finance_source(catalog,history,parts,annual,fake))
        ahead=copy.deepcopy(history)
        ahead["asof"]=(date.fromisoformat(catalog["date"])+timedelta(days=1)).isoformat()
        self.assertIsNone(finance_source(catalog,ahead,parts,annual,audit))
        forged=copy.deepcopy(annual)
        forged["official_exchange_pbr_verified"]=True
        with self.assertRaises(LivePartialScorecardError):
            finance_source(catalog,history,parts,forged,audit)

    def test_real_saved_market_reconciles_without_rescanning_prices(self):
        docs=DOCS
        required=("data.json","v22-market-catalog.json",
                  "v3-volume-obv-observed.json",
                  "v3-current-price-technical-momentum.json")
        sources=[json.loads((docs/name).read_text(encoding="utf8"))
                 for name in required]
        # Independent scheduled collectors can publish on different sessions.
        # Never weaken production same-day verification to make a fixture pass.
        if (sources[0].get("date")!=sources[1].get("date") or
            sources[2].get("asof")!=sources[1].get("date") or
            sources[3].get("asof")!=sources[1].get("date")):
            self.skipTest("stored_current_sources_not_same_trading_session")
        # A collector can update its same-day snapshot before its sibling;
        # a saved fixture is only replayable when exact recomputation matches.
        if (sources[2]!=volume_build(sources[0],sources[1]) or
            sources[3]!=price_build(sources[0],sources[1])):
            self.skipTest("stored_source_snapshots_not_exactly_replayed")
        # After the next scheduled full-market scan a dated 121-session
        # source requires its independently committed same-snapshot audit.
        # Exercise that real path instead of testing only pre-scan fixtures.
        path_receipt=None
        if sources[0].get("v3_unscored_path_source") is not None:
            path_receipt=json.loads((docs/"v3-unscored-path-coverage.json").read_text(
                encoding="utf8"))
        rows,report=generate(*sources,path_coverage_receipt=path_receipt)
        self.assertEqual(len(rows),report["listed_total"])
        self.assertEqual(report["listed_total"],sources[1]["listed_total"])
        self.assertEqual(report["asof"],sources[0]["date"])
        self.assertEqual(report["observed_issuer_counts"]["volume"],
                         sources[2]["observed_volume_candidate_issuers"])
        self.assertEqual(report["observed_issuer_counts"]["technical"],
                         sources[3]["technical_research_issuer_count"])
        self.assertEqual(report["observed_issuer_counts"]["momentum"],
                         sources[3]["momentum_research_issuer_count"])
        self.assertEqual(report["certified_100point_count"],0)
        self.assertEqual(report["observed_issuer_counts"]["financial"],0)
        self.assertTrue(all(r["available"]<=(60 if path_receipt else 49)
                            for r in rows))
        self.assertEqual(report["path_independent_saved_audit_sha256"],
                         path_receipt["daily_source_sha256"] if path_receipt else None)


    def test_fcf_collection_completion_triggers_same_day_rescore(self):
        flow=(Path(__file__).resolve().parents[1]/
              ".github/workflows/v3-current-observed-scorecard.yml").read_text(
                  encoding="utf8")
        self.assertIn("workflow_run:",flow)
        self.assertIn("- Full-Market 100-Stock Batches",flow)
        self.assertIn("잠정 100점 관측배점 충족 종목",flow)
        self.assertIn("정식 투자점수 100점 인증 종목",flow)
        self.assertIn(
            "V3 Actual DART 3Y Cashflow and CAPEX (strict source-only pilot)",flow)
        self.assertIn("types: [completed]",flow)
        self.assertIn("workflow_run.conclusion == 'success'",flow)
        self.assertIn("workflow_run.head_branch == 'main'",flow)
        self.assertIn("workflow_run.head_repository.full_name == github.repository",flow)
        self.assertIn("V3 Bulk OpenDART Four-Year Listed History (100 issuers per request)",flow)
        self.assertIn("cron: '11 * * * *'",flow)
        self.assertNotIn("cron: '26,56 * * * *'",flow)
        self.assertNotIn("DART_API_KEY:",flow)
        self.assertNotIn("KRX_API_KEY:",flow)


if __name__=="__main__":
    unittest.main()
