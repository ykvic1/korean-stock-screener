"""Synthetic quality-gated walk-forward integration tests, no real prices or APIs."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import json
import unittest

from v3_walkforward_quality_gate import gated_walkforward, ResearchGateError
from v3_historical_integrity import SESSION
from v3_walkforward_research import HORIZONS


def trading_days(start, count):
    current = date.fromisoformat(start)
    days = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def flow_history(end):
    d = date.fromisoformat(end)
    days = []
    while len(days) < 250:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return [
        {"date": day, "session": SESSION, "market": "KOSPI",
         "foreign_net_shares": 1, "institution_total_net_shares": 2,
         "pension_net_shares": 0}
        for day in reversed(days)
    ]


def candidate(signal_idx=15, history=252):
    days = trading_days("2024-01-02", 320)
    signal = days[signal_idx]
    identity = {"code": "000001", "issuer_id": "00000001", "market": "KOSPI"}
    bars = [
        {"date": d, "open": 100.0, "high": 102.0, "low": 98.0,
         "close": 100.0, "volume": 1000,
         "trading_status": "active", "session": SESSION}
        for d in days
    ]
    record = {**identity, "listed_on": "2020-01-02",
              "delisted_on": None, "bars": bars, "events": []}
    case_bars = [
        {k: b[k] for k in ("date", "session", "open", "high", "low", "close")}
        for b in bars[signal_idx + 1: signal_idx + 1 + history]
    ]
    case = {
        **identity, "signal_date": signal,
        "decision_at_utc": signal + "T07:10:00Z",
        "stop_price": 90, "target_price": 110, "bars": case_bars,
        "evidence": {
            "pit_universe_asserted": True, "regular_session_asserted": True,
            "corporate_actions_audited_asserted": True,
            "trading_calendar_complete_asserted": True,
            "signal_setup_frozen_asserted": True,
            "signal_snapshot_sha256": "a" * 64,
        },
    }
    flow = {
        **identity, "asof": signal,
        "observed_at_utc": signal + "T07:00:00Z",
        "ledger": {
            **identity, "institution_definition": "total_includes_pension",
            "pension_definition": "pension_etc_subcategory",
            "observations": flow_history(signal),
        }
    }
    pit = {
        **identity,
        "roster": {"asof": signal, "members": ["00000001"],
                   "observed_at_utc": signal + "T06:40:00Z"},
        "price": {
            **identity, "asof": signal, "session": SESSION,
            "raw_sha256": "b" * 64,
            "observed_at_utc": signal + "T06:50:00Z",
        },
        "flow_ledger": flow,
        "filings": [{
            **identity, "rcept_no": "20230301000001", "scope": "CFS",
            "period_end": "2022-12-31", "revision_no": 0,
            "published_at_utc": "2023-03-01T02:00:00Z",
            "observed_at_utc": "2023-03-01T03:00:00Z",
            "source_sha256": "c" * 64,
        }],
        "sector": {
            **identity, "sector_code": "A.01", "valid_from": "2020-01-01",
            "valid_to": None, "observed_at_utc": signal + "T06:10:00Z",
        },
    }
    return {"case": case, "price_record": record, "pit_bundle": pit}


def research(rows):
    return gated_walkforward(rows, split_date="2026-01-01")


class WalkForwardQualityBridgeTests(unittest.TestCase):
    def test_clean_internal_match_still_not_reliable_investment_forecast(self):
        a = research([candidate()])
        self.assertEqual(a["input_case_count"], 1)
        self.assertEqual(set(a["periods"]), set(HORIZONS))
        self.assertEqual(a["periods"]["12m"]["internally_eligible_cases_submitted"], 1)
        self.assertEqual(a["periods"]["12m"]["exploratory_walkforward"]["train"]["n"], 1)
        self.assertIsNone(a["periods"]["12m"]["exploratory_walkforward"]["train"]["mean_historical_net_pct"])
        self.assertFalse(a["corporate_actions_officially_certified"])
        self.assertFalse(a["survivorship_and_censoring_bias_resolved"])
        self.assertFalse(a["model_parameters_frozen_out_of_sample"])
        self.assertTrue(a["historical_results_are_not_expected_returns"])
        self.assertIsNone(a["confirmed_forecast"])
        self.assertIsNone(a["confirmed_v3_score"])
        self.assertFalse(a["v3_release_ready"])
        self.assertFalse(a["confirmed_buy"])

    def test_event_in_later_horizon_censors_only_affected_labels(self):
        payload = candidate()
        record = payload["price_record"]
        d = record["bars"][60]["date"]
        record["events"].append({
            "effective_date": d, "kind": "split", "source_sha256": "d" * 64
        })
        r = research([payload])
        self.assertEqual(r["periods"]["1m"]["internally_eligible_cases_submitted"], 1)
        self.assertEqual(r["periods"]["2m"]["internally_eligible_cases_submitted"], 1)
        for h in ("3m", "6m", "12m"):
            self.assertEqual(r["periods"][h]["internally_eligible_cases_submitted"], 0)
            self.assertEqual(r["periods"][h]["quality_exclusion_counts"],
                             {"post_signal_label_quality_censored": 1})
        self.assertFalse(r["survivorship_and_censoring_bias_resolved"])

    def test_event_at_signal_blocks_every_horizon(self):
        payload = candidate()
        record = payload["price_record"]
        signal = payload["case"]["signal_date"]
        record["events"].append({
            "effective_date": signal, "kind": "merger", "source_sha256": "e" * 64
        })
        r = research([payload])
        for h in HORIZONS:
            self.assertEqual(r["periods"][h]["internally_eligible_cases_submitted"], 0)
            self.assertEqual(r["periods"][h]["quality_exclusion_counts"],
                             {"signal_within_corporate_event_quarantine": 1})

    def test_price_gap_in_label_is_quarantined_not_adj_close_fabricated(self):
        payload = candidate()
        record = payload["price_record"]
        record["bars"][80].update(open=200.0, high=203, low=199, close=201)
        # Same OHLCV immutable source must match case after the edit.
        bar = record["bars"][80]
        signal_idx = 15
        claim = payload["case"]["bars"][80 - signal_idx - 1]
        for f in ("open", "high", "low", "close"):
            claim[f] = bar[f]
        r = research([payload])
        self.assertEqual(r["periods"]["12m"]["internally_eligible_cases_submitted"], 0)
        self.assertEqual(r["periods"]["12m"]["quality_exclusion_counts"],
                         {"post_signal_label_quality_censored": 1})
        self.assertFalse(r["corporate_actions_officially_certified"])

    def test_modified_adjusted_or_gapped_case_bars_rejected(self):
        changes = {
            "adjusted_close": lambda p: p["case"]["bars"][10].update(close=101.0),
            "fake_volume_unrelated": lambda p: p["case"]["bars"][10].update(open=99.0),
            "skip_day": lambda p: p["case"]["bars"][3].update(
                date=p["case"]["bars"][4]["date"]),
        }
        for name, change in changes.items():
            p = candidate()
            change(p)
            with self.subTest(name=name), self.assertRaises(ResearchGateError):
                research([p])

    def test_issuer_code_reuse_or_wrong_market_fails_closed(self):
        for component, key, wrong in (
            ("case", "issuer_id", "00000002"),
            ("pit_bundle", "code", "000002"),
            ("price_record", "market", "KOSDAQ"),
        ):
            p = candidate()
            p[component][key] = wrong
            with self.subTest(component=component), self.assertRaises(ResearchGateError):
                research([p])

    def test_asof_source_join_retrieved_after_signal_excludes_all(self):
        p = candidate()
        p["pit_bundle"]["price"]["observed_at_utc"] = "2025-01-01T07:00:00Z"
        r = research([p])
        for h in HORIZONS:
            self.assertEqual(r["periods"][h]["internally_eligible_cases_submitted"], 0)
            self.assertEqual(r["periods"][h]["quality_exclusion_counts"],
                             {"point_in_time_metadata_unavailable": 1})

    def test_missing_future_rows_are_right_censored_not_promoted(self):
        p = candidate(history=20)
        r = research([p])
        self.assertEqual(r["periods"]["1m"]["quality_exclusion_counts"],
                         {"right_censored_short_history": 1})
        self.assertEqual(r["periods"]["1m"]["exploratory_walkforward"]["train"]["right_censored"], 1)
        self.assertIsNone(r["periods"]["1m"]["exploratory_walkforward"]["train"]["mean_historical_net_pct"])
        self.assertFalse(r["v3_release_ready"])

    def test_duplicate_signal_of_same_issuer_is_rejected(self):
        a = candidate()
        with self.assertRaisesRegex(ResearchGateError, "duplicate_issuer_signal"):
            research([a, copy.deepcopy(a)])

    def test_corrupt_price_record_blocks_not_silently_skipped(self):
        p = candidate()
        p["price_record"]["bars"][40]["low"] = -100
        with self.assertRaisesRegex(ResearchGateError, "unusable_historical_research_input"):
            research([p])

    def test_empty_research_reports_no_forecast(self):
        r = research([])
        self.assertEqual(r["input_case_count"], 0)
        self.assertEqual(r["periods"]["12m"]["internally_eligible_cases_submitted"], 0)
        self.assertIsNone(r["confirmed_forecast"])
        self.assertFalse(r["confirmed_buy"])

    def test_no_sensitive_raw_numbers_or_provider_keys_in_report(self):
        p = candidate()
        p["pit_bundle"]["flow_ledger"]["ledger"]["observations"][0][
            "foreign_net_shares"] = 123456789
        r = research([p])
        text = json.dumps(r)
        self.assertNotIn("123456789", text)
        self.assertNotIn("raw_sha256", text)
        self.assertFalse(r["v3_release_ready"])


if __name__ == "__main__":
    unittest.main()
