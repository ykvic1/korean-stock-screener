"""Keep one valuation per security and retain the historical scope boundaries."""

import unittest

from v3_historical_operating_scope import HORIZONS, count_valuations
from v3_historical_performance_pilot_audit import PilotAuditError


class HistoricalOperatingScopeTests(unittest.TestCase):
    def test_action_supersedes_raw_value_and_settlements_stay_separate(self):
        stable = {str(h): set() for h in HORIZONS}
        stable["3"] = {"raw", "entitlement", "preferred"}
        adjusted = {str(h): {"rows": [], "count": 0} for h in HORIZONS}
        adjusted["3"] = {"rows": [
            {"code": "entitlement", "valuation_includes_unlisted_entitlement": True},
            {"code": "split", "valuation_includes_unlisted_entitlement": False},
        ], "count": 2}
        settlements = [
            {"code": "settled", "horizon_months": "3", "value_per_entry_share_krw": "100"},
            {"code": "unresolved", "horizon_months": "3", "value_per_entry_share_krw": ""},
        ]
        cohorts = {
            "all": {"raw", "entitlement", "preferred", "split", "settled", "unresolved"},
            "operating": {"raw", "entitlement", "split", "settled", "unresolved"},
        }

        report = count_valuations(stable, adjusted, settlements, cohorts)["3"]

        self.assertEqual(report["operating"], {
            "raw_continuous_only": 1,
            "action_adjusted": 2,
            "overlap_superseded": 1,
            "non_delisted_unique": 3,
            "delisting_settlement": 1,
            "includes_unlisted_entitlement": 1,
        })
        self.assertEqual(report["all"]["non_delisted_unique"], 4)

        settlements[0]["code"] = "entitlement"
        with self.assertRaisesRegex(PilotAuditError, "historical_scope_delisting_overlap"):
            count_valuations(stable, adjusted, settlements, cohorts)
