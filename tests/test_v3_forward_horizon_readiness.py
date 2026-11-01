"""No-network synthetic frozen-cohort forward-horizon readiness safeguards."""
from copy import deepcopy
import json
import unittest

from v3_forward_horizon_readiness import (
    COHORT_SCHEMA, PROBE_SCHEMA, ForwardAuditError, HORIZONS,
    audit_forward_readiness,
)

ASSESSMENT = "2026-11-01T09:00:00Z"
DECISION = "2025-01-31T07:00:00Z"


def fixture():
    cohort = {
        "schema": COHORT_SCHEMA, "signal_date": "2025-01-31",
        "decision_at_utc": DECISION,
        "selection_sha256": "a" * 64,
        "historical_issuer_pit_verified": False,
        "signal_score_certified": False,
        "members": [
            {"code": "000001", "issuer_id": "00000001", "market": "KOSPI"},
            {"code": "000002", "issuer_id": "00000002", "market": "KOSDAQ"},
        ],
    }
    probes = {
        "schema": PROBE_SCHEMA, "signal_date": "2025-01-31",
        "selection_sha256": "a" * 64,
        "adjusted_returns_certified": False,
        "corporate_actions_certified": False,
        "rows": [],
    }
    return cohort, probes


def close_probe(code="000001", issuer="00000001", market="KOSPI",
                months=1, day="2025-02-28"):
    return {
        "code": code, "issuer_id": issuer, "market": market,
        "horizon_months": months, "state": "observed_close",
        "source_sha256": "b" * 64,
        "retrieved_at_utc": "2025-03-01T01:00:00Z",
        "observed_date": day,
        "adjusted_regular_session_verified": False,
        "total_return_corporate_actions_verified": False,
    }


def delisted_probe():
    row = close_probe(code="000002", issuer="00000002", market="KOSDAQ")
    del row["observed_date"]
    row["state"] = "delisted_unresolved"
    row["delisted_on"] = "2025-02-15"
    return row


def assess(data, *, assessed=ASSESSMENT):
    return audit_forward_readiness(*data, assessed_at_utc=assessed)


class ForwardReadinessTests(unittest.TestCase):
    def test_all_five_horizons_keep_full_frozen_denominator(self):
        data = fixture()
        data[1]["rows"] = [close_probe(), delisted_probe()]
        out = assess(data)
        self.assertEqual(tuple(map(int, out["horizons"])), HORIZONS)
        self.assertEqual(out["frozen_cohort_count"], 2)
        self.assertEqual(out["horizons"]["1"], {
            "full_frozen_cohort_count": 2, "not_yet_matured": 0,
            "observed_close_unverified": 1,
            "delisted_return_unresolved": 1,
            "source_missing_or_unverified": 0,
        })
        self.assertEqual(out["horizons"]["2"]["source_missing_or_unverified"], 2)
        self.assertFalse(out["backtest_performance_authorized"])
        self.assertFalse(out["investment_score_or_buy_signal_authorized"])
        self.assertFalse(out["independent_pit_universe_verified"])
        self.assertTrue(out["delisted_outcomes_not_ignored_or_zero_filled"])
        self.assertEqual(out["provider_calls"], 0)
        self.assertNotIn("000001", json.dumps(out))
        self.assertNotIn("00000001", json.dumps(out))

    def test_unmatured_horizons_are_pending_not_failed_or_zero(self):
        out = assess(fixture(), assessed="2025-02-15T09:00:00Z")
        for horizon in HORIZONS:
            self.assertEqual(out["horizons"][str(horizon)]["not_yet_matured"], 2)
            self.assertEqual(out["horizons"][str(horizon)]["source_missing_or_unverified"], 0)

    def test_anniversary_clamps_month_end_and_late_close_rejected(self):
        data = fixture()
        data[1]["rows"].append(close_probe(day="2025-02-28"))
        self.assertEqual(assess(data)["horizons"]["1"]["observed_close_unverified"], 1)
        for day in ("2025-02-27", "2025-03-11", "2025-05-01"):
            data = fixture()
            data[1]["rows"] = [close_probe(day=day)]
            with self.subTest(day=day), self.assertRaises(ForwardAuditError):
                assess(data)

    def test_unmatured_outcome_must_not_appear_from_future(self):
        data = fixture()
        data[1]["rows"].append(close_probe())
        with self.assertRaisesRegex(ForwardAuditError, "after_assessment"):
            assess(data, assessed="2025-02-15T09:00:00Z")

    def test_duplicate_horizon_or_wrong_issuer_cannot_contaminate_census(self):
        data = fixture()
        data[1]["rows"] = [close_probe(), close_probe()]
        with self.assertRaisesRegex(ForwardAuditError, "duplicate_horizon"):
            assess(data)
        for key, value in (("issuer_id", "00000002"), ("market", "KOSDAQ"),
                           ("code", "999999")):
            data = fixture()
            row = close_probe()
            row[key] = value
            data[1]["rows"] = [row]
            with self.subTest(key=key), self.assertRaises(ForwardAuditError):
                assess(data)

    def test_overlapping_or_relisted_identity_is_rejected(self):
        data = fixture()
        data[0]["members"][1]["issuer_id"] = "00000001"
        with self.assertRaisesRegex(ForwardAuditError, "duplicate_or_remapped"):
            assess(data)
        data = fixture()
        data[0]["members"][1]["code"] = "000001"
        with self.assertRaisesRegex(ForwardAuditError, "duplicate_or_remapped"):
            assess(data)

    def test_unverified_certifications_never_become_official(self):
        for key, container in (
            ("historical_issuer_pit_verified", 0),
            ("signal_score_certified", 0),
            ("adjusted_returns_certified", 1),
            ("corporate_actions_certified", 1),
        ):
            data = fixture()
            data[container][key] = True
            with self.subTest(field=key), self.assertRaises(ForwardAuditError):
                assess(data)
        for flag in ("adjusted_regular_session_verified",
                     "total_return_corporate_actions_verified"):
            data = fixture()
            row = close_probe()
            row[flag] = True
            data[1]["rows"].append(row)
            with self.subTest(flag=flag), self.assertRaises(ForwardAuditError):
                assess(data)

    def test_no_forged_future_timestamp_or_unexpected_price_fields(self):
        for change in (
            {"retrieved_at_utc": "2025-01-31T06:59:59Z"},
            {"retrieved_at_utc": "2027-01-01T00:00:00Z"},
            {"retrieved_at_utc": "2025-02-28T05:00:00Z"},
            {"horizon_months": True},
            {"horizon_months": 9},
            {"source_sha256": "0" * 63},
            {"raw_close": 123},
        ):
            data = fixture()
            data[1]["rows"] = [{**close_probe(), **change}]
            with self.subTest(change=change), self.assertRaises(ForwardAuditError):
                assess(data)

    def test_delisted_outcome_remains_unresolved_and_strictly_dated(self):
        data = fixture()
        data[1]["rows"].append(delisted_probe())
        self.assertEqual(assess(data)["horizons"]["1"]["delisted_return_unresolved"], 1)
        for change in (
            {"delisted_on": "2025-01-31"},
            {"delisted_on": "2025-03-01"},
            {"retrieved_at_utc": "2025-02-01T00:00:00Z"},
            {"observed_date": "2025-02-28"},
        ):
            data = fixture()
            data[1]["rows"] = [{**delisted_probe(), **change}]
            with self.subTest(change=change), self.assertRaises(ForwardAuditError):
                assess(data)

    def test_frozen_cohort_time_and_hash_must_match(self):
        for key, value in (("signal_date", "2025-02-01"),
                           ("selection_sha256", "f" * 64)):
            data = fixture()
            data[1][key] = value
            with self.assertRaises(ForwardAuditError):
                assess(data)
        data = fixture()
        data[0]["decision_at_utc"] = "2025-01-31T06:29:59Z"
        with self.assertRaises(ForwardAuditError):
            assess(data)
        data = fixture()
        with self.assertRaises(ForwardAuditError):
            assess(data, assessed="2025-01-31T06:59:59Z")


if __name__ == "__main__":
    unittest.main()
