"""Offline PIT join regression. All receipts, tickers and amounts are synthetic."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import json
import unittest

from v3_pit_join import PITJoinError, join_asof_evidence, SCHEMA
from v3_ias_flow_ledger import SESSION

ASOF = "2026-10-16"
DECISION = "2026-10-16T07:10:00Z"


def mock_ledger():
    d = date.fromisoformat(ASOF)
    rows = []
    while len(rows) < 250:
        if d.weekday() < 5:
            rows.append({
                "date": d.isoformat(), "session": SESSION,
                "market": "KOSPI", "foreign_net_shares": 7,
                "institution_total_net_shares": 11, "pension_net_shares": 3,
            })
        d -= timedelta(days=1)
    return {
        "code": "000001", "issuer_id": "00000001", "market": "KOSPI",
        "institution_definition": "total_includes_pension",
        "pension_definition": "pension_etc_subcategory",
        "observations": list(reversed(rows)),
    }


def base():
    identity = {"code": "000001", "issuer_id": "00000001", "market": "KOSPI"}
    return {
        **identity,
        "roster": {"asof": ASOF, "observed_at_utc": "2026-10-16T06:45:00Z",
                   "members": ["00000001", "00000002"]},
        "price": {**identity, "asof": ASOF, "session": SESSION,
                  "raw_sha256": "a" * 64,
                  "observed_at_utc": "2026-10-16T06:40:00Z"},
        "flow_ledger": {**identity, "asof": ASOF,
                        "observed_at_utc": "2026-10-16T06:50:00Z",
                        "ledger": mock_ledger()},
        "filings": [
            {**identity, "rcept_no": "20260301000001",
             "period_end": "2025-12-31", "scope": "CFS", "revision_no": 0,
             "source_sha256": "b" * 64,
             "published_at_utc": "2026-03-01T01:00:00Z",
             "observed_at_utc": "2026-03-01T02:00:00Z"},
            {**identity, "rcept_no": "20260401000002",
             "period_end": "2025-12-31", "scope": "CFS", "revision_no": 1,
             "source_sha256": "c" * 64,
             "published_at_utc": "2026-04-01T02:00:00Z",
             "observed_at_utc": "2026-04-01T03:00:00Z"},
        ],
        "sector": {**identity, "sector_code": "IND.010",
                   "valid_from": "2025-01-01", "valid_to": None,
                   "observed_at_utc": "2026-10-16T05:00:00Z"},
    }


def run(bundle=None, decision=DECISION):
    return join_asof_evidence(bundle if bundle is not None else base(),
                              asof=ASOF, decision_at_utc=decision)


class PITJoinTests(unittest.TestCase):
    def test_everything_aligned_is_still_research_only(self):
        report = run()
        self.assertEqual(report["schema"], SCHEMA)
        self.assertTrue(report["join_date_identity_consistent"])
        self.assertTrue(report["all_inputs_retrieved_by_signal"])
        self.assertTrue(report["roster"]["observed_no_later_than_decision"])
        self.assertTrue(report["price"]["same_day_observed_before_decision"])
        self.assertEqual(report["flow"]["observed_session_rows"], 250)
        self.assertEqual(report["flow"]["fully_observed_windows"],
                         ["20", "60", "120", "250"])
        self.assertEqual(report["financials"]["chosen_scope_research_only"], "CFS")
        self.assertEqual(report["financials"]["eligible_filing_periods"],
                         ["2025-12-31"])
        self.assertFalse(report["source_provenance_independently_authenticated"])
        self.assertFalse(report["financial_values_pit_verified"])
        self.assertFalse(report["backtesting_authorized"])
        self.assertFalse(report["v3_release_ready"])
        self.assertFalse(report["confirmed_buy"])
        self.assertIsNone(report["v3_six_pillar_confirmed_score"])

    def test_post_signal_amendment_is_ignored_not_backfilled(self):
        b = base()
        late = copy.deepcopy(b["filings"][1])
        late["rcept_no"] = "20261017000003"
        late["revision_no"] = 2
        late["published_at_utc"] = "2026-10-16T08:10:00Z"
        late["observed_at_utc"] = "2026-10-16T09:00:00Z"
        b["filings"].append(late)
        r = run(b)
        self.assertEqual(r["financials"]["future_or_post_signal_filings_ignored"], 1)
        self.assertEqual(r["financials"]["chosen_scope_research_only"], "CFS")
        self.assertFalse(r["financials"]["historical_revision_chain_authenticated"])

    def test_filing_downloaded_after_signal_cannot_certify_historical_pit(self):
        b = base()
        for p in b["filings"]:
            p["observed_at_utc"] = "2026-10-17T02:00:00Z"
        r = run(b)
        self.assertEqual(r["financials"]["future_or_post_signal_filings_ignored"], 2)
        self.assertIsNone(r["financials"]["chosen_scope_research_only"])
        self.assertEqual(r["financials"]["eligible_filing_periods"], [])
        self.assertFalse(r["all_inputs_retrieved_by_signal"])

    def test_cfs_preferred_over_ofs_without_stitching(self):
        b = base()
        b["filings"].append({
            **{k: b[k] for k in ("code", "issuer_id", "market")},
            "rcept_no": "20260405000006", "scope": "OFS",
            "period_end": "2026-03-31", "revision_no": 0,
            "published_at_utc": "2026-04-05T01:00:00Z",
            "observed_at_utc": "2026-04-05T02:00:00Z",
            "source_sha256": "d" * 64,
        })
        report = run(b)
        self.assertEqual(report["financials"]["chosen_scope_research_only"], "CFS")
        self.assertEqual(report["financials"]["eligible_filing_periods"],
                         ["2025-12-31"])

    def test_preclose_signal_or_next_day_timestamp_is_rejected(self):
        for when in ("2026-10-16T06:29:59Z", "2026-10-16T15:01:00Z",
                     "2026-10-15T07:00:00Z", "2026-10-16T16:00:00+09:00"):
            with self.subTest(when=when), self.assertRaises(PITJoinError):
                run(decision=when)

    def test_roster_from_future_or_unknown_issuer_is_not_allowed(self):
        b = base()
        b["roster"]["observed_at_utc"] = "2026-10-17T00:00:00Z"
        r = run(b)
        self.assertFalse(r["roster"]["observed_no_later_than_decision"])
        self.assertFalse(r["all_inputs_retrieved_by_signal"])
        b = base()
        b["roster"]["members"] = ["00000002"]
        with self.assertRaisesRegex(PITJoinError, "security_not_in_historical_roster"):
            run(b)

    def test_price_flow_and_sector_post_signal_do_not_get_ready(self):
        for dimension in ("price", "flow_ledger", "sector"):
            b = base()
            b[dimension]["observed_at_utc"] = "2026-10-17T00:00:00Z"
            r = run(b)
            with self.subTest(dimension=dimension):
                self.assertFalse(r["all_inputs_retrieved_by_signal"])
                self.assertFalse(r["confirmed_buy"])

    def test_stale_flow_price_or_sector_never_silent_alignment(self):
        for dimension in ("price", "flow_ledger"):
            b = base()
            b[dimension]["asof"] = "2026-10-15"
            with self.subTest(dimension=dimension), self.assertRaises(PITJoinError):
                run(b)
        b = base()
        b["flow_ledger"]["ledger"]["observations"].pop()
        with self.assertRaisesRegex(PITJoinError, "flow_ledger_different_issuer_or_stale"):
            run(b)
        b = base()
        b["sector"]["valid_to"] = ASOF
        r = run(b)
        self.assertFalse(r["sector"]["membership_date_matched_before_decision"])

    def test_wrong_issuer_and_cross_market_never_joined(self):
        for field, altered in (("issuer_id", "00000002"),
                               ("code", "000002"), ("market", "KOSDAQ")):
            for dimension in ("price", "flow_ledger", "sector"):
                b = base()
                b[dimension][field] = altered
                with self.subTest(dimension=dimension, field=field), self.assertRaisesRegex(
                        PITJoinError, "cross_issuer_or_market_contamination"):
                    run(b)

    def test_broken_nested_flow_taxonomy_is_rejected(self):
        b = base()
        b["flow_ledger"]["ledger"]["pension_definition"] = "other"
        with self.assertRaisesRegex(PITJoinError, "unusable_flow_ledger"):
            run(b)
        b = base()
        b["flow_ledger"]["ledger"]["observations"][-1]["foreign_net_shares"] = None
        r = run(b)
        self.assertNotIn("20", r["flow"]["fully_observed_windows"])
        self.assertFalse(r["v3_release_ready"])

    def test_fake_flags_do_not_grant_trading_or_backtesting(self):
        b = base()
        b.update({"official_data_ready": True, "score": 100,
                  "confirmed_buy": True, "v3_release_ready": True})
        r = run(b)
        self.assertFalse(r["confirmed_buy"])
        self.assertFalse(r["backtesting_authorized"])
        self.assertIsNone(r["v3_six_pillar_confirmed_score"])
        self.assertNotIn("foreign_net_shares", json.dumps(r))

    def test_invalid_duplicate_receipts_versions_and_financial_timestamps(self):
        changes = {
            "receipt_duplicate": lambda b: b["filings"].append(
                copy.deepcopy(b["filings"][0])),
            "version_ambiguity": lambda b: b["filings"][1].update(revision_no=0),
            "before_period": lambda b: b["filings"][0].update(
                published_at_utc="2024-02-01T00:00:00Z"),
            "before_publication": lambda b: b["filings"][0].update(
                observed_at_utc="2026-02-01T00:00:00Z"),
            "wrong_receipt": lambda b: b["filings"][0].update(rcept_no="0000"),
            "invalid_scope": lambda b: b["filings"][0].update(scope="MIXED"),
            "boolean_revision": lambda b: b["filings"][0].update(revision_no=True),
            "bad_hash": lambda b: b["filings"][0].update(source_sha256="unknown"),
        }
        for label, alter in changes.items():
            b = base()
            alter(b)
            with self.subTest(label=label), self.assertRaises(PITJoinError):
                run(b)

    def test_missing_sources_are_explicitly_null_not_fabricated(self):
        b = base()
        b["flow_ledger"] = None
        b["price"] = None
        b["filings"] = []
        b["sector"] = None
        r = run(b)
        self.assertFalse(r["all_inputs_retrieved_by_signal"])
        self.assertIsNone(r["flow"]["observed_session_rows"])
        self.assertIsNone(r["financials"]["chosen_scope_research_only"])
        self.assertFalse(r["price"]["record_present"])
        self.assertFalse(r["sector"]["membership_date_matched_before_decision"])
        self.assertFalse(r["v3_release_ready"])


if __name__ == "__main__":
    unittest.main()
