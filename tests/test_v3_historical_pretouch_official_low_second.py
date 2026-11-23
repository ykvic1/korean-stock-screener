"""Second KRX 5-date LOW-only continuation and fail-closed source provenance."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, Mock

import v3_historical_pretouch_official_low_second as second


class SecondPreFirstLowTests(TestCase):
    def test_frozen_next_five_dates_are_already_ranked_and_not_old_dates(self):
        self.assertEqual(second.EXPECTED_DATES, (
            "2025-11-28", "2025-12-02", "2025-12-03",
            "2025-12-04", "2025-12-05"))
        self.assertEqual(second.EXPECTED_HOLDER_DAYS, 35)
        self.assertEqual(second.BATCH_LIMIT, 5)
        self.assertTrue(second.SECOND_FROZEN.name.endswith("20261123.json"))

    def test_no_double_krx_queries_if_second_evidence_exists(self):
        sentinel = {"report": {"pre_touch_holder_days_on_existing_official_ohlc_dates": 215,
                               "pre_touch_holder_days_without_existing_official_ohlc_date": 537,
                               "distinct_pre_touch_official_low_missing_dates": 189,
                               "bounded_next_official_request_dates": list(second.EXPECTED_DATES),
                               "bounded_next_issuer_low_observations": 35},
                    "batch": [date.fromisoformat(d) for d in second.EXPECTED_DATES]}
        with patch.object(second, "verified_frozen_coverage", return_value=sentinel):
            with patch.object(second, "saved_official_ohlc_dates", return_value=set()):
                self.assertIs(second.second_batch_plan({}), sentinel)
            with patch.object(second, "SECOND_FROZEN", Mock(**{"exists.return_value": True})):
                with patch.object(second, "check_bounded_batch") as network:
                    with self.assertRaises(ValueError):
                        second.probe_second({}, object())
                    network.assert_not_called()

    def test_invalid_issuer_count_and_missing_horizon_prerequisite_fail_closed(self):
        bad = {"report": {"pre_touch_holder_days_on_existing_official_ohlc_dates": 215,
                           "pre_touch_holder_days_without_existing_official_ohlc_date": 537,
                           "distinct_pre_touch_official_low_missing_dates": 189,
                           "bounded_next_official_request_dates": list(second.EXPECTED_DATES),
                           "bounded_next_issuer_low_observations": 34},
               "batch": [date.fromisoformat(d) for d in second.EXPECTED_DATES]}
        with patch.object(second, "verified_frozen_coverage", return_value=bad):
            with patch.object(second, "saved_official_ohlc_dates", return_value=set()):
                with self.assertRaises(ValueError):
                    second.second_batch_plan({})

    def test_schema_and_provenance_must_match_actual_authenticated_batch(self):
        with patch.object(second, "second_batch_plan", return_value={"report": {}}):
            with self.assertRaises(ValueError):
                second.valid_second_evidence({}, {}, provenance=(1,2,3,"0" * 64))
            with self.assertRaises(ValueError):
                second.valid_second_evidence({}, {
                    "schema": second.SCHEMA, "saved_on_kst": "2026-11-23",
                    "claim_frozen_readonly": True,
                    "run_id": 8, "job_id": 9, "artifact_id": 10,
                    "artifact_sha256": "f" * 64
                }, provenance=(1,2,3,"0" * 64))

    def test_regression_uses_existing_archive_without_additional_calls(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "v3_historical_pretouch_official_low_second.py").read_text()
        self.assertIn("verified_frozen_coverage", source)
        self.assertIn("saved_official_ohlc_dates", source)
        self.assertIn("check_bounded_batch", source)
        self.assertIn("already_authenticated_second_pre_first_low_batch", source)
        self.assertIn("first_touch_earliest_low_certified", source)


if __name__ == "__main__":
    import unittest
    unittest.main()
