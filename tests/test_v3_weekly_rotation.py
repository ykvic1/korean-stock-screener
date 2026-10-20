"""No-provider seven-day V3 cohort execution regression tests."""
import unittest
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from datetime import date, timedelta

from v3_weekly_rotation import (
    EPOCH, RotationError, GROUPS, cycle_for, recalculate, technical_points,
)


def fixture(n=2497):
    codes = [f"{i:06d}" for i in range(n)]
    rows = [{"code": code, "name": f"회사{i}", "market": "KOSPI" if i % 2 else "KOSDAQ",
             "price_history_status": "priced" if i != 0 else "price_source_or_validation_error",
             "sector": "IT" if i % 3 else None}
            for i, code in enumerate(codes)]
    stock = []
    for i, code in enumerate(codes[1:], 1):
        v3 = {
            "schema": "v3-pattern-within-six-pillars-research-0.4",
            "asof": "2026-10-19", "technical_maximum": 25,
            "buy_certified": False, "v3_release_ready": False,
            "investable_score": None, "technical_observed_maximum": 11,
            "technical_research_candidate": None,
            "technical_parts": {
                "daily_pattern": 5.0, "weekly_pattern": None,
                "daily_rsi": 1.0, "weekly_rsi": None,
                "weekly_ma120": None, "volume_confirmation": 2.0,
            },
        }
        if i == 3:
            v3["technical_parts"] = {k: None for k in v3["technical_parts"]}
            v3["technical_observed_maximum"] = 0
        stock.append({"code": code,"market":"KOSPI" if i%2 else "KOSDAQ",
                      "price_date": "2026-10-19",
                      "price20": 3.4, "price60": 7.2,
                      "relative20": i / 100, "relative60": -i / 200,
                      "flows_confirmed": False,
                      "v3_pattern_integrated": v3})
    market = {"status": "success", "date": "2026-10-19",
              "index_available":["KOSPI","KOSDAQ"], "stocks": stock}
    catalog = {"schema": "v22-current-listed-catalog-0.1",
               "date": "2026-10-19", "listed_total": n, "rows": rows}
    book = {"schema": "v3-dart-current-listed-annual-book-reference-0.1",
            "asof": "2026-10-19", "market_listed_total": n,
            "historical_point_in_time_verified": False,
            "official_exchange_pbr_verified": False,
            "records": [
                {"code": "000001", "status": "observed", "annual": {
                    "year": 2025, "fs_div": "OFS",
                    "receipt_id": "20260331000001", "values": {"equity": 1000}}}
            ]}
    return market, catalog, book


class RotationTests(unittest.TestCase):
    def test_cohorts_run_once_per_seven_days_and_coverage(self):
        market, catalog, book = fixture()
        state = None
        self.assertEqual(sum([357]*5 + [356]*2), 2497)
        for n in range(GROUPS):
            d = (EPOCH + timedelta(days=n)).isoformat()
            state, report = recalculate(market, catalog, book, when=d, prior=state)
            self.assertEqual(report["processed_today"], [357]*5+[356]*2 and
                             (357 if n < 5 else 356))
            self.assertEqual(report["processed_this_cycle"], sum(
                report["cohort_counts"][:n+1]))
            self.assertEqual(report["cohort_counts"], [357,357,357,357,357,356,356])
            self.assertEqual(report["verified_full_six_pillar_issuer_count"], 0)
            self.assertEqual(report["all_issuers_updated_this_cycle"], n == 6)
            self.assertEqual(report["source_collection_calls"], 0)
        self.assertEqual(len(state["records"]), 2497)
        self.assertEqual(report["processed_this_cycle"], 2497)
        self.assertEqual(report["unprocessed_this_cycle"], 0)
        self.assertTrue(report["all_issuers_updated_this_cycle"])
        # Legitimate unverified technical points and missing source are distinct.
        self.assertIsNone(state["records"]["000000"]["research_score_points"])
        self.assertEqual(state["records"]["000000"]["available_weight"], 0)
        self.assertEqual(state["records"]["000001"]["research_score_points"], 8)
        self.assertEqual(state["records"]["000001"]["available_weight"], 11)
        self.assertEqual(state["records"]["000001"]["momentum_available_weight"], 8)
        self.assertTrue(0 < state["records"]["000001"]["momentum_research_points"] <= 8)
        self.assertEqual(state["records"]["000001"]["research_provisional_available_weight"],19)
        self.assertEqual(report["source_reference_counts_this_cycle"]["momentum_partial"],2496)
        self.assertEqual(state["records"]["000003"]["research_score_points"], None)
        self.assertTrue(state["records"]["000001"]["source_observed"]["annual_receipt"])
        self.assertFalse(state["records"]["000001"]["six_pillar_research_score_confirmed"])
        self.assertFalse(state["legacy_v22_score_used"])
        self.assertEqual(state["source_collection_calls"], 0)
        # Same day replay must not rescore or double-count.
        again, report2 = recalculate(market, catalog, book,
                                     when=(EPOCH+timedelta(days=6)).isoformat(),
                                     prior=state)
        self.assertEqual(report2["processed_today"], 0)
        self.assertEqual(again["processed_this_cycle"], 2497)
        next_cycle, next_report = recalculate(market, catalog, book,
                                              when=(EPOCH+timedelta(days=7)).isoformat(),
                                              prior=state)
        self.assertEqual(next_report["processed_today"], 357)
        self.assertEqual(next_report["processed_this_cycle"], 357)
        self.assertEqual(next_cycle["records"]["000000"]["cycle"],
                         next_cycle["cycle_start"])
        self.assertEqual(next_cycle["records"]["000001"]["cycle"],
                         state["cycle_start"])

    def test_backfill_real_saved_pattern_without_v2_score_imputation(self):
        from test_v3_pattern_composite_score import stock
        from v3_weekly_rotation import existing_evidence_adapter, inspect_issuer
        row = stock("000001")
        row["price_date"] = "2026-10-19"
        row["pattern_research"]["asof"] = "2026-10-19"
        row["score"] = 0.0  # legacy score MUST NOT affect technical research.
        self.assertIsNone(row.get("v3_pattern_integrated"))
        adapter, rebuilt = existing_evidence_adapter(row,"2026-10-19")
        self.assertTrue(rebuilt)
        self.assertAlmostEqual(adapter["technical_research_candidate"],24.04)
        self.assertEqual(technical_points(row,"2026-10-19")[:2],(24.04,25))
        listed={"code":"000001","name":"시험","market":"KOSPI","sector":"IT"}
        record=inspect_issuer("000001",market_asof="2026-10-19",
            catalog_row=listed,price_row=row,annual_row=None,four_row=None,
            inspected_on="2026-10-20")
        self.assertTrue(record["technical_rebuilt_from_saved_source"])
        self.assertEqual(record["research_score_points"],24.04)
        self.assertEqual(record["available_weight"],25)
        self.assertIsNone(record["technical_research_backfill"]["investable_score"])
        self.assertFalse(record["six_pillar_research_score_confirmed"])
        row["score"]=99
        self.assertEqual(technical_points(row,"2026-10-19")[:2],(24.04,25))
        row["pattern_research"]["asof"]="2026-10-20"
        result=technical_points(row,"2026-10-19")
        self.assertEqual(result[1],16, "stale daily pattern cannot contribute points")
        row["v3_pattern_integrated"]={"schema":"wrong"}
        self.assertIsNone(technical_points(row,"2026-10-19")[0],
          "invalid provided adapter must never be bypassed by rebuilding")
        row["price_date"]="2026-10-18"
        self.assertIsNone(technical_points(row,"2026-10-19")[0])

    def test_mismatch_dates_negative_or_stale_rejected(self):
        market,catalog,book = fixture(13)
        bad = {**catalog, "date": "2026-10-18"}
        with self.assertRaises(RotationError):
            recalculate(market,bad,book,when=EPOCH.isoformat())
        with self.assertRaises(RotationError):
            recalculate(market,catalog,book,when="2026-11-01")
        with self.assertRaises(RotationError):
            recalculate(market,catalog,book,when="2026-10-19")

    def test_unverified_invalid_part_does_not_become_zero(self):
        market,catalog,book = fixture(14)
        row = market["stocks"][0]
        self.assertEqual(technical_points(row, "2026-10-19")[:2],(8,11))
        row["v3_pattern_integrated"]["technical_parts"]["daily_pattern"] = 9.0
        self.assertIsNone(technical_points(row, "2026-10-19")[0])
        row["v3_pattern_integrated"]["technical_parts"]["daily_pattern"] = 0
        self.assertEqual(technical_points(row, "2026-10-19")[:2],(3,11))
        row["price_date"] = "2026-10-18"
        self.assertIsNone(technical_points(row, "2026-10-19")[0])

    def test_universe_growth_reconciles_and_catches_up(self):
        market,catalog,book=fixture(14)
        first,report = recalculate(market,catalog,book,when=EPOCH.isoformat())
        self.assertEqual(report["processed_today"],2)
        # Append a new stock on the next day in the SAME week.
        new="000014"
        catalog["rows"].append({"code":new,"name":"신규","market":"KOSPI",
                                "price_history_status":"priced","sector":"IT"})
        catalog["listed_total"] += 1
        market["stocks"].append({"code":new,"price_date":market["date"],
                                 "price20":None,"price60":None,
                                 "v3_pattern_integrated":None})
        book["market_listed_total"] += 1
        second,report= recalculate(market,catalog,book,
                                   when=(EPOCH+timedelta(days=1)).isoformat(),
                                   prior=first)
        self.assertEqual(report["listed_total"],15)
        self.assertEqual(report["processed_this_cycle"],5)
        self.assertIn(new,second["records"])
        self.assertEqual(second["records"][new]["available_weight"],0)


    def test_source_fingerprints_are_order_independent_but_value_sensitive(self):
        from v3_weekly_rotation import source_input_fingerprints
        market,catalog,book=fixture(14)
        first=source_input_fingerprints(market,catalog,book)
        self.assertEqual(first,source_input_fingerprints(
            {k:market[k] for k in reversed(list(market))},
            {k:catalog[k] for k in reversed(list(catalog))},
            {k:book[k] for k in reversed(list(book))}))
        self.assertIsNone(first["four_year"])
        updated=copy.deepcopy(book)
        updated["records"][0]["annual"]["values"]["equity"]+=1
        newer=source_input_fingerprints(market,catalog,updated)
        self.assertNotEqual(first["annual"],newer["annual"])
        self.assertEqual(first["market"],newer["market"])

    def test_post_rebase_verify_blocks_same_date_dart_and_price_edits(self):
        from v3_weekly_rotation import main
        market,catalog,book=fixture(14)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def save(name,data):
                path=root/name
                path.write_text(json.dumps(data,ensure_ascii=False),encoding="utf8")
                return str(path)
            m=save("market.json",market)
            c=save("catalog.json",catalog)
            a=save("annual.json",book)
            args=["--market",m,"--catalog",c,"--annual",a,
                  "--four",str(root/"missing-four.json"),
                  "--state",str(root/"state.json"),
                  "--report",str(root/"coverage.json"),
                  "--when",EPOCH.isoformat()]
            with redirect_stdout(io.StringIO()):
                main(args+["--mode","publish"])
                main(args+["--mode","verify"])
            original_state=(root/"state.json").read_bytes()
            original_report=(root/"coverage.json").read_bytes()
            changed=copy.deepcopy(book)
            changed["records"][0]["annual"]["values"]["equity"]+=1
            save("annual.json",changed)
            with self.assertRaisesRegex(RotationError,"post_rebase_source_inputs_changed"):
                with redirect_stdout(io.StringIO()):
                    main(args+["--mode","verify"])
            save("annual.json",book)
            changed_market=copy.deepcopy(market)
            changed_market["stocks"][0]["price20"]=9.9
            save("market.json",changed_market)
            with self.assertRaisesRegex(RotationError,"post_rebase_source_inputs_changed"):
                with redirect_stdout(io.StringIO()):
                    main(args+["--mode","verify"])
            self.assertEqual(original_state,(root/"state.json").read_bytes())
            self.assertEqual(original_report,(root/"coverage.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
