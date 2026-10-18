"""Synthetic-only PIT roster and corporate-action triage regression; no API/network."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import unittest

from v3_historical_integrity import (
    IntegrityError, SESSION, audit_universe, audit_price_events
)


def registry():
    return [
        {"issuer_id": "00000001", "code": "123456", "market": "KOSDAQ",
         "ordinary_share": True, "listed_on": "2020-01-02",
         "delisted_on": "2023-02-01"},
        {"issuer_id": "00000002", "code": "789012", "market": "KOSPI",
         "ordinary_share": True, "listed_on": "2020-01-02",
         "delisted_on": None},
        # Same ticker code, but a new corporate issuer after prior delisting.
        {"issuer_id": "00000003", "code": "123456", "market": "KOSDAQ",
         "ordinary_share": True, "listed_on": "2023-02-01",
         "delisted_on": None},
    ]


def snapshots():
    return [
        {"asof": "2022-01-10", "members": [
            {"issuer_id": "00000001", "code": "123456", "market": "KOSDAQ"},
            {"issuer_id": "00000002", "code": "789012", "market": "KOSPI"},
        ]},
        {"asof": "2024-01-03", "members": [
            {"issuer_id": "00000002", "code": "789012", "market": "KOSPI"},
            {"issuer_id": "00000003", "code": "123456", "market": "KOSDAQ"},
        ]},
    ]


def record(count=40):
    bars = []
    d = date(2022, 1, 3)
    while len(bars) < count:
        if d.weekday() < 5:
            bars.append({"date": d.isoformat(), "open": 100.0,
                         "high": 102.0, "low": 98.0, "close": 100.0,
                         "volume": 1000, "session": SESSION,
                         "trading_status": "active"})
        d += timedelta(days=1)
    return {"issuer_id": "00000001", "code": "123456", "market": "KOSDAQ",
            "listed_on": "2020-01-02", "delisted_on": "2023-02-01",
            "bars": bars, "events": []}


class HistoricalIntegrityTests(unittest.TestCase):
    def test_delisted_are_in_past_roster_and_reused_ticker_is_distinct_issuer(self):
        r = audit_universe(registry(), snapshots(), asof="2024-01-05")
        self.assertTrue(r["internal_lifecycle_consistent"])
        self.assertEqual(r["declared_registry_issuers"], 3)
        self.assertEqual(r["distinct_later_delisted_issuers_in_rosters"], 1)
        self.assertEqual(r["ticker_reuse_observed"], [
            {"market": "KOSDAQ", "code": "123456", "distinct_issuer_count": 2}])
        self.assertEqual(r["date_snapshots"][0]["member_count"], 2)
        self.assertEqual(r["identity_grouping"], "issuer_id_not_code")
        self.assertFalse(r["point_in_time_universe_certified"])
        self.assertFalse(r["survivorship_bias_resolved"])
        self.assertFalse(r["usable_for_v3_backtest"])
        self.assertIsNone(r["confirmed_v3_score"])

    def test_registry_and_snapshot_order_do_not_change_audit_digest(self):
        a = audit_universe(registry(), snapshots(), asof="2024-01-05")
        b = audit_universe(list(reversed(registry())),
                           list(reversed(snapshots())), asof="2024-01-05")
        self.assertEqual(a["input_canonical_sha256_for_change_detection"],
                         b["input_canonical_sha256_for_change_detection"])

    def test_survivor_only_backfill_is_rejected(self):
        snap = snapshots()
        snap[0]["members"] = snap[0]["members"][1:]
        with self.assertRaisesRegex(IntegrityError,
                                    "active_registry_member_missing_from_roster"):
            audit_universe(registry(), snap, asof="2024-01-05")

    def test_no_duplicate_code_identity_or_future_members(self):
        mutations = {
            "fake_current_survivor": lambda reg, snaps: snaps[0]["members"].append(
                {"issuer_id": "00000003", "code": "123456", "market": "KOSDAQ"}),
            "renamed_issuer": lambda reg, snaps: snaps[0]["members"][0].update(
                code="111111"),
            "duplicate_member": lambda reg, snaps: snaps[0]["members"].append(
                copy.deepcopy(snaps[0]["members"][0])),
            "duplicate_snapshot": lambda reg, snaps: snaps.append(copy.deepcopy(snaps[0])),
            "future_roster": lambda reg, snaps: snaps[1].update(asof="2027-01-03"),
            "bad_listing": lambda reg, snaps: reg[0].update(
                delisted_on="2020-01-02"),
            "duplicated_issuer": lambda reg, snaps: reg[2].update(
                issuer_id="00000002"),
            "overlap_reused_ticker": lambda reg, snaps: reg[2].update(
                listed_on="2023-01-31"),
            "preferred_stock": lambda reg, snaps: reg[0].update(
                ordinary_share=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                reg, snaps = registry(), snapshots()
                mutate(reg, snaps)
                with self.assertRaises(IntegrityError):
                    audit_universe(reg, snaps, asof="2024-01-05")

    def test_no_events_does_not_mean_officially_verified(self):
        p = audit_price_events(record(), asof="2022-12-30")
        self.assertEqual(p["observed_bars"], 40)
        self.assertEqual(p["boundary_flags"], [])
        self.assertEqual(p["excluded_signal_dates"], [])
        self.assertEqual(len(p["segments_not_spliced"]), 1)
        self.assertIsNone(p["adjusted_or_filled_prices"])
        self.assertFalse(p["official_corporate_events_verified"])
        self.assertFalse(p["regular_session_price_and_volume_verified"])
        self.assertFalse(p["usable_for_v3_backtest"])

    def test_declared_split_quarantines_surrounding_rows_not_backadjusting(self):
        inp = record()
        day = inp["bars"][12]["date"]
        inp["events"] = [{"effective_date": day, "kind": "split",
                          "source_sha256": "a" * 64}]
        p = audit_price_events(inp, asof="2022-12-30")
        self.assertEqual(p["declared_actions"], 1)
        self.assertEqual(p["segments_not_spliced"][0]["observed_rows"], 12)
        self.assertEqual(p["segments_not_spliced"][1]["observed_rows"], 28)
        self.assertEqual(len(p["excluded_signal_dates"]), 5)
        self.assertEqual(p["excluded_signal_dates"][2], day)
        self.assertIn("declared_split", p["boundary_flags"][0]["reasons"])
        self.assertFalse(p["split_ratio_inferred_from_price"])
        self.assertIsNone(p["adjusted_or_filled_prices"])

    def test_large_jump_is_candidate_not_proven_split(self):
        inp = record()
        inp["bars"][10].update(open=200, high=205, low=198, close=200)
        # A price level discontinuity can be a genuine event OR supplier defect.
        # Consecutive jump back also gets a candidate, never an auto repair.
        p = audit_price_events(inp, asof="2022-12-30")
        self.assertEqual(len(p["large_move_candidates"]), 2)
        self.assertGreaterEqual(len(p["segments_not_spliced"]), 2)
        self.assertTrue(all("not_verified" in x["reason"]
                            for x in p["large_move_candidates"]))
        self.assertFalse(p["full_history_verified"])

    def test_calendar_gap_not_interpolated_or_called_holiday(self):
        inp = record()
        inp["bars"] = inp["bars"][:10] + [
            {**b, "date": (date.fromisoformat(b["date"]) +
                            timedelta(days=25)).isoformat()}
            for b in inp["bars"][10:]
        ]
        p = audit_price_events(inp, asof="2022-12-30")
        self.assertEqual(len(p["unverified_calendar_gap_candidates"]), 1)
        self.assertEqual(len(p["segments_not_spliced"]), 2)
        self.assertFalse(p["full_trading_calendar_verified"])

    def test_halt_and_next_observation_block_research_signal(self):
        inp = record()
        inp["bars"][10].update(volume=0, trading_status="halted")
        p = audit_price_events(inp, asof="2022-12-30")
        dates = {x["date"] for x in p["boundary_flags"]}
        self.assertIn(inp["bars"][10]["date"], dates)
        self.assertIn(inp["bars"][11]["date"], dates)
        self.assertIn(inp["bars"][10]["date"], p["excluded_signal_dates"])
        self.assertFalse(p["usable_for_v3_backtest"])

    def test_bar_hash_changes_with_raw_value_without_mutating_it(self):
        inp = record()
        orig = copy.deepcopy(inp)
        a = audit_price_events(inp, asof="2022-12-30")
        self.assertEqual(inp, orig)
        inp["bars"][7]["volume"] = 1001
        b = audit_price_events(inp, asof="2022-12-30")
        self.assertNotEqual(a["input_bar_sha256_for_change_detection"],
                            b["input_bar_sha256_for_change_detection"])
        self.assertFalse(b["full_history_verified"])

    def test_invalid_bars_events_and_listing_cutoff_fail_closed(self):
        mutations = {
            "zero_ohlc": lambda p: p["bars"][0].update(open=0),
            "bool_close": lambda p: p["bars"][0].update(close=True),
            "nan": lambda p: p["bars"][0].update(high=float("nan")),
            "outside_range": lambda p: p["bars"][0].update(low=101),
            "negative_volume": lambda p: p["bars"][0].update(volume=-1),
            "bool_volume": lambda p: p["bars"][0].update(volume=True),
            "halted_trade": lambda p: p["bars"][0].update(trading_status="halted"),
            "unknown_session": lambda p: p["bars"][0].update(session="AFTER_HOURS"),
            "out_of_order": lambda p: p["bars"][1].update(
                date=p["bars"][0]["date"]),
            "after_delisting": lambda p: p.update(delisted_on="2022-01-05"),
            "before_listing": lambda p: p.update(listed_on="2022-02-01"),
            "future_event": lambda p: p["events"].append({
                "effective_date": "2027-01-01", "kind": "split",
                "source_sha256": "a" * 64}),
            "bad_digest": lambda p: p["events"].append({
                "effective_date": "2022-01-06", "kind": "split",
                "source_sha256": "guess"}),
            "invented_action": lambda p: p["events"].append({
                "effective_date": "2022-01-06", "kind": "mystery",
                "source_sha256": "a" * 64}),
            "duplicate_event": lambda p: p["events"].extend([
                {"effective_date": "2022-01-06", "kind": "split",
                 "source_sha256": "a" * 64} for _ in range(2)]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                inp = record()
                mutate(inp)
                with self.assertRaises(IntegrityError):
                    audit_price_events(inp, asof="2022-12-30")


if __name__ == "__main__":
    unittest.main()
