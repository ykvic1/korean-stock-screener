"""Synthetic tests for in-memory private KRX-price-panel bridge.

Never connects to KRX, reads private personal inputs or publishes OHLCV.
"""
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from v3_krx_private_price_bridge import (
    build_private_price_panels, PrivatePanelError, main)
from v3_local_private_store import store_pair
from v3_price_feature_adapter import extract_price_features, PriceFeatureError

D0="2026-10-15"
D1="2026-10-16"


def stock(day, code, *, open_price="100", high="110", low="90",
          close="103", volume="500"):
    return {"BAS_DD":day.replace("-",""),"ISU_CD":code,
            "TDD_OPNPRC":open_price,"TDD_HGPRC":high,
            "TDD_LWPRC":low,"TDD_CLSPRC":close,"ACC_TRDVOL":volume}


def payload(*rows):
    return json.dumps({"OutBlock_1": list(rows)}).encode()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"private"/"markets.sqlite3"

    def archive(self, day, *, kpi=None, kdq=None):
        if kpi is None:
            kpi=payload(stock(day,"005930"),
                        stock(day,"00088K",open_price="0",high="0",low="0",
                              close="109",volume="0"))
        if kdq is None:
            kdq=payload(stock(day,"247540"))
        store_pair(database_path=self.db,asof=day,
                   kospi_raw=kpi,kosdaq_raw=kdq,
                   kospi_received_at_utc=day+"T07:31:00Z",
                   kosdaq_received_at_utc=day+"T07:31:01Z")

    def panel(self, *, targets=None, **kw):
        return build_private_price_panels(
            start=kw.pop("start",D0),end=kw.pop("end",D1),
            private_db=kw.pop("private_db",self.db),
            calendar_source=kw.pop("calendar_source","weekdays"),
            targets=targets or [{"market":"KOSPI","code":"005930"}],
            **kw)

    def test_missing_archive_no_io_and_blocked_scoring(self):
        r=self.panel()
        self.assertFalse(self.db.exists())
        s=r["summary"]
        self.assertEqual(s["actual_api_calls"],0)
        self.assertEqual(s["candidate_dates_total_unverified"],2)
        self.assertEqual(s["archived_two_market_pairs"],0)
        self.assertEqual(s["observed_valid_price_rows_for_targets"],0)
        self.assertFalse(s["eligible_for_investment_score"])
        self.assertFalse(s["v3_release_ready"])

    def test_two_market_observations_are_in_memory_only(self):
        self.archive(D0)
        self.archive(D1)
        r=self.panel(targets=[{"market":"KOSPI","code":"005930"},
                              {"market":"KOSDAQ","code":"247540"}])
        s=r["summary"]
        self.assertEqual(s["archived_two_market_pairs"],2)
        self.assertEqual(s["observed_valid_price_rows_for_targets"],4)
        self.assertEqual(s["targets_with_any_valid_rows"],2)
        self.assertEqual(s["targets_with_250_contiguous_candidate_rows"],0)
        self.assertEqual(s["targets_with_128_observed_distinct_weeks"],0)
        bars=r["panels"][("KOSPI","005930")]["observed_bars"]
        self.assertEqual(len(bars),2)
        self.assertEqual(bars[-1]["close"],103)
        self.assertEqual(bars[-1]["aggregate_acc_trdvol_observed"],500)
        self.assertNotIn("volume",bars[-1])
        self.assertNotIn("session",bars[-1])
        self.assertFalse(s["eligible_for_v3_price_feature_adapter"])
        self.assertFalse(s["weekly_120_ma_source_verified"])
        # Current verified-session price adapter must FAIL, not accept this.
        with self.assertRaises(PriceFeatureError):
            extract_price_features(bars,asof=D1)
        rendered=json.dumps(s)
        for forbidden in ("005930","247540","103","TDD_CLSPRC",str(self.db)):
            self.assertNotIn(forbidden,rendered)

    def test_price_only_extracts_official_close_and_no_volume(self):
        self.archive(D0)
        self.archive(D1)
        r=self.panel(price_only=True)
        bars=r["panels"][("KOSPI","005930")]["observed_bars"]
        self.assertEqual([bar["close"] for bar in bars],[103,103])
        self.assertEqual(r["summary"]["observed_valid_price_rows_for_targets"],2)
        self.assertEqual(r["summary"]["price_selection"],
                         "krx_tdd_clsprc_regular_close_only")
        self.assertTrue(r["summary"]["aggregate_volume_ignored_for_price_acceptance"])
        for bar in bars:
            self.assertEqual(set(bar),{
                "date","close","source_scope",
                "corporate_action_adjustment_verified",
                "issuer_identity_verified","regular_only_volume_verified"})
            self.assertEqual(bar["source_scope"],
                             "KRX_OFFICIAL_DAILY_REGULAR_CLOSE_ONLY")
            self.assertNotIn("volume",bar)
            self.assertNotIn("session",bar)
        self.assertFalse(r["summary"]["eligible_for_investment_score"])
        with self.assertRaises(PriceFeatureError):
            extract_price_features(bars,asof=D1)

    def test_price_only_salvages_close_when_combined_volume_missing(self):
        self.archive(D0,kpi=payload(
            stock(D0,"005930",volume="-"),stock(D0,"005931")))
        self.archive(D1)
        # The older complete-OHLCV research excludes the malformed total.
        default=self.panel()["panels"][("KOSPI","005930")]
        self.assertEqual([bar["date"] for bar in default["observed_bars"]],[D1])
        self.assertEqual(default["quarantined_dates"],[D0])
        # Price-only evidence is independent of that combined total field.
        price=self.panel(price_only=True)["panels"][("KOSPI","005930")]
        self.assertEqual([bar["date"] for bar in price["observed_bars"]],[D0,D1])
        self.assertEqual(price["quarantined_dates"],[])
        self.assertTrue(all("aggregate_acc_trdvol_observed" not in bar
                            for bar in price["observed_bars"]))

    def test_price_only_quarantines_invalid_close_even_when_volume_is_valid(self):
        self.archive(D0,kpi=payload(
            stock(D0,"005930",close="-"),stock(D0,"005931")))
        self.archive(D1)
        panel=self.panel(price_only=True)["panels"][("KOSPI","005930")]
        self.assertEqual([bar["date"] for bar in panel["observed_bars"]],[D1])
        self.assertEqual(panel["quarantined_dates"],[D0])

    def test_zero_price_is_quarantined_not_filled_or_replaced(self):
        self.archive(D0)
        self.archive(D1)
        p=self.panel(targets=[{"market":"KOSPI","code":"00088K"}])["panels"][
            ("KOSPI","00088K")]
        self.assertEqual(len(p["observed_bars"]),0)
        self.assertEqual(p["quarantined_dates"],[D0,D1])
        self.assertEqual(p["present_in_archived_pairs"],2)
        self.assertEqual(p["missing_in_archived_pairs"],0)
        self.assertFalse(p["complete_candidate_pair_coverage"])

    def test_absent_and_quarantined_are_not_same(self):
        self.archive(D0)
        self.archive(D1,kpi=payload(stock(D1,"005930")))
        p=self.panel(targets=[{"market":"KOSPI","code":"00088K"}])["panels"][
            ("KOSPI","00088K")]
        self.assertEqual(p["missing_in_archived_pairs"],1)
        self.assertEqual(p["quarantined_dates"],[D0])
        self.assertEqual(len(p["observed_bars"]),0)

    def test_no_code_identity_assumed_and_no_fake_historical_data(self):
        self.archive(D0)
        r=self.panel()
        p=r["panels"][("KOSPI","005930")]
        self.assertEqual(len(p["observed_bars"]),1)
        self.assertEqual(p["missing_in_archived_pairs"],0)
        self.assertFalse(p["complete_candidate_pair_coverage"])
        self.assertEqual(r["summary"]["candidate_dates_total_unverified"],2)
        self.assertFalse(r["summary"]["historical_issuer_identity_verified"])

    def test_bad_target_and_more_than_100_are_rejected(self):
        for targets in ([],[{"market":"KOSPI","code":"005930"}]*2,
                        [{"market":"KOSPI","code":"BAD?"}],
                        [{"market":"KOSPI","code":"005930","name":"x"}],
                        [{"market":"KOSPI","code":"005930"}]*101):
            with self.subTest(n=len(targets)):
                with self.assertRaises(PrivatePanelError):
                    build_private_price_panels(
                        start=D0,end=D1,private_db=self.db,
                        targets=targets,calendar_source="weekdays")
        self.assertFalse(self.db.exists())

    def test_price_only_requires_an_explicit_boolean(self):
        with self.assertRaisesRegex(PrivatePanelError,
                                    "invalid_price_only_selection"):
            self.panel(price_only="yes")
        self.assertFalse(self.db.exists())

    def test_bad_stored_source_cannot_generate_any_panels(self):
        self.archive(D0,kpi=payload(stock(D1,"005930")))
        with self.assertRaisesRegex(PrivatePanelError,
                                    "private_archive_reaudit_required"):
            self.panel()

    def test_cli_exposes_only_aggregates_even_when_target_is_present(self):
        self.archive(D0)
        output=StringIO()
        with redirect_stdout(output):
            result=main(["--start",D0,"--end",D1,
                         "--private-db",str(self.db),
                         "--calendar-source","weekdays",
                         "--market","KOSPI","--code","005930"])
        self.assertEqual(result,0)
        shown=output.getvalue()
        self.assertIn('"requested_targets": 1',shown)
        for forbidden in ("005930","247540","TDD_OPNPRC",str(self.db)):
            self.assertNotIn(forbidden,shown)
        self.assertNotIn('"panels"',shown)

    def test_100_distinct_targets_batched_without_network(self):
        self.archive(D0)
        targets=[{"market":"KOSPI","code":f"{i:06d}"} for i in range(100)]
        r=self.panel(targets=targets)
        self.assertEqual(r["summary"]["requested_targets"],100)
        self.assertEqual(len(r["panels"]),100)
        self.assertEqual(r["summary"]["actual_api_calls"],0)
        self.assertFalse(r["summary"]["v3_release_ready"])


if __name__=="__main__":
    unittest.main()
