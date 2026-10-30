"""No-network contracts for weekly official DART source freshness checking."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import unittest

import v3_dart_weekly_freshness as fresh
import v3_dart_bulk_history as bulk
from test_v3_dart_bulk_history import FakeDART, CODES
from test_v3_dart_rolling_book_reference import fixture

KEY = "synthetic-key-not-secret-123"
TODAY = date(2026, 10, 20)


def archived():
    market, catalog = fixture()
    source = FakeDART()
    history, calls = bulk.collect(
        market, catalog, None, key=KEY, session=source, groups=1)
    assert calls == 5
    return market, catalog, history


class SevenDaySourceAuditTests(unittest.TestCase):
    def test_exactly_once_across_seven_days_and_four_daily_parts(self):
        for n in (1, 5, 49, 356, 2497):
            codes = [f"{i:06d}" for i in range(n)]
            selected = []
            per_day = []
            for offset in range(7):
                one_day = []
                for part in range(4):
                    day_index, subset = fresh.slice_codes(
                        codes, TODAY + timedelta(days=offset), part)
                    self.assertEqual(day_index, offset)
                    self.assertLessEqual(len(subset), 90)
                    one_day.extend(subset)
                    selected.extend(subset)
                per_day.append(len(one_day))
            self.assertEqual(len(selected), n)
            self.assertEqual(len(set(selected)), n)
            self.assertEqual(set(selected), set(codes))
            self.assertLessEqual(max(per_day) - min(per_day), 1)
        with self.assertRaisesRegex(fresh.AuditError, "provider_batch_too_large"):
            fresh.slice_codes([f"{i:06d}" for i in range(2600)], TODAY, 0)
        with self.assertRaises(fresh.AuditError):
            fresh.slice_codes(CODES, TODAY, 4)

    def test_no_official_requests_in_audit_mode_and_no_mutations(self):
        market, catalog, history = archived()
        preserved = copy.deepcopy(history)
        caller = FakeDART()
        result = fresh.run(
            market, catalog, history, when=TODAY, part=0, mode="audit",
            key=KEY, session=caller)
        self.assertEqual(caller.calls, [])
        self.assertEqual(result["source_api_calls"], 0)
        self.assertEqual(result["status"], "bounded_rotation_plan_only")
        self.assertFalse(result["source_archive_edited"])
        self.assertFalse(result["historical_PIT_certified"])
        self.assertEqual(result["fiscal_years"], [2022, 2023, 2024, 2025])
        self.assertEqual(history, preserved)

    def test_live_rechecks_four_years_in_memory_and_never_edits_archive(self):
        market, catalog, history = archived()
        preserved = copy.deepcopy(history)
        caller = FakeDART()
        result = fresh.run(
            market, catalog, history, when=TODAY, part=0, mode="live",
            key=KEY, session=caller)
        self.assertEqual(len(caller.calls), 5)  # corpCode + 4 actual fiscal GETs
        self.assertEqual(result["source_api_calls"], 5)
        self.assertEqual(result["status"], "same_source_confirmed")
        self.assertEqual(result["comparison_counts"]["unchanged"], 4)
        self.assertEqual(result["drift_flags"], [])
        self.assertEqual(history, preserved)

    def test_drift_and_missing_are_quarantined_never_assumed_zero(self):
        market, catalog, history = archived()
        caller = FakeDART(blank_year=2024)
        result = fresh.run(
            market, catalog, history, when=TODAY, part=0, mode="live",
            key=KEY, session=caller)
        self.assertEqual(result["status"], "source_drift_quarantined")
        self.assertEqual(result["comparison_counts"]
                         ["previously_observed_now_missing"], 1)
        self.assertEqual(result["drift_flags"][0]["fiscal_year"], 2024)
        self.assertNotIn("values", str(result))
        # A changed receipt is evidence of a drift, not a safe automatic overwrite.
        previous = {"005930": {"years": {"2025": {
            "CFS": {"receipt_id": "20260318000001", "values": {"revenue": 3}}}}}}
        newer = {"005930": {"2025": {
            "CFS": {"receipt_id": "20260418000001", "values": {"revenue": 4}}}}}
        counts, flags = fresh.changes(previous, newer, ["005930"], [2025])
        self.assertEqual(counts["revised"], 1)
        self.assertEqual(flags[0]["reason"], "revised")

    def test_incomplete_archive_never_makes_source_calls(self):
        market, catalog, history = archived()
        history["records"][CODES[0]]["years"].pop("2023")
        caller = FakeDART()
        result = fresh.run(
            market, catalog, history, when=TODAY, part=0, mode="live",
            key=KEY, session=caller)
        self.assertEqual(result["status"], "waiting_for_first_complete_dart_backfill")
        self.assertEqual(result["source_api_calls"], 0)
        self.assertEqual(caller.calls, [])

    def test_prior_day_roster_change_waits_without_official_calls(self):
        market, catalog, history = archived()
        preserved = copy.deepcopy(history)
        market["date"] = TODAY.isoformat()
        catalog["date"] = TODAY.isoformat()
        for row in market["stocks"]:
            row["price_date"] = TODAY.isoformat()
        # A yesterday-snapshot with one fewer issuer is a valid independent
        # collector lag, not today's auditable financial source universe.
        history["records"].pop(CODES[-1])
        history["listed_total"] -= 1
        history["attempted_company_count"] -= 1
        caller = FakeDART()
        report = fresh.run(market, catalog, history, when=TODAY,
                           part=0, mode="live", key=KEY, session=caller)
        self.assertEqual(report["status"], "waiting_for_same_roster_annual_history")
        self.assertEqual(report["source_api_calls"], 0)
        self.assertEqual(report["selected_company_count"], 0)
        self.assertFalse(report["historical_PIT_certified"])
        self.assertFalse(report["buy_signals_authorized"])
        self.assertEqual(caller.calls, [])

        # A same-day mismatch or a forged prior count is never deferred.
        history["asof"] = TODAY.isoformat()
        with self.assertRaisesRegex(fresh.AuditError, "untrusted_annual"):
            fresh.run(market, catalog, history, when=TODAY,
                      part=0, mode="audit")
        history["asof"] = preserved["asof"]
        history["listed_total"] += 1
        with self.assertRaisesRegex(fresh.AuditError, "untrusted_annual"):
            fresh.run(market, catalog, history, when=TODAY,
                      part=0, mode="audit")

    def test_quota_and_invalid_sources_fail_closed(self):
        market, catalog, history = archived()
        caller = FakeDART(fail_year=2023)
        with self.assertRaisesRegex(fresh.AuditError, "source_or_quota_stop"):
            fresh.run(market, catalog, history, when=TODAY,
                      part=0, mode="live", key=KEY, session=caller)
        self.assertEqual(len(caller.calls), 3)
        self.assertEqual(len(history["records"]), len(CODES))
        wrong = copy.deepcopy(history)
        wrong["listed_total"] += 1
        with self.assertRaisesRegex(fresh.AuditError, "untrusted_annual"):
            fresh.run(market, catalog, wrong, when=TODAY,
                      part=0, mode="audit")
        with self.assertRaisesRegex(fresh.AuditError, "stale_or_future"):
            fresh.run(market, catalog, history,
                      when=TODAY + timedelta(days=11), part=0, mode="audit")


if __name__ == "__main__":
    unittest.main()
