"""Real-published-source offline DART/market date-lag audit, no calls."""
import copy
from datetime import date,timedelta
import json
from pathlib import Path
import unittest

from v3_dart_offline_lag_plan import report,OfflineDARTAuditError


def sources():
    docs=Path("docs")
    return (json.loads((docs/"data.json").read_text(encoding="utf8")),
            json.loads((docs/"v22-market-catalog.json").read_text(encoding="utf8")),
            json.loads((docs/"v3-dart-book-reference.json").read_text(encoding="utf8")),
            json.loads((docs/"v3-dart-four-year-reference.json").read_text(encoding="utf8")))


class DateLagOfflinePilotTests(unittest.TestCase):
    def test_actual_market_day_vs_older_dart_book_is_explicitly_blocked_not_fabricated(self):
        market,catalog,book,prior=sources()
        today=date.fromisoformat(market["date"])
        old=(today-timedelta(days=1)).isoformat()
        book=copy.deepcopy(book)
        book["asof"]=old
        r=report(market,catalog,book,prior=prior)
        self.assertEqual(r["candidate_plan_status"],"blocked_not_same_day_census")
        self.assertEqual(r["annual_book_lag_days"],1)
        self.assertIsNone(r["next_manual_pilot_cohort_count"])
        self.assertFalse(r["full_financial15_verified"])
        self.assertEqual(r["api_calls"],0)
        self.assertTrue(r["unverified_values_never_zero_filled"])
        self.assertEqual(r["listed_issuers"],catalog["listed_total"])

    def test_same_day_readonly_cohort_plan_when_annual_book_catches_up(self):
        market,catalog,book,prior=sources()
        revised=copy.deepcopy(book)
        revised["asof"]=market["date"]
        # A caught-up annual book must include the new actual listed census.
        revised["market_listed_total"]=catalog["listed_total"]
        r=report(market,catalog,revised,prior=prior,limit=2)
        self.assertEqual(r["annual_book_lag_days"],0)
        self.assertEqual(r["candidate_plan_status"],
                         "ready_same_day_sources_unverified_fundamentals")
        self.assertLessEqual(r["next_manual_pilot_cohort_count"],2)
        self.assertEqual(r["api_calls"],0)
        self.assertFalse(r["full_financial15_verified"])

    def test_future_book_or_cached_fouryear_is_not_silently_used(self):
        market,catalog,book,prior=sources()
        tomorrow=(date.fromisoformat(market["date"])+timedelta(days=1)).isoformat()
        b=copy.deepcopy(book)
        b["asof"]=tomorrow
        with self.assertRaisesRegex(OfflineDARTAuditError,
                                    "future_annual_book_must_not_be_used"):
            report(market,catalog,b,prior=prior)
        p=copy.deepcopy(prior)
        p["asof"]=tomorrow
        with self.assertRaisesRegex(OfflineDARTAuditError,
                                    "future_fouryear_source_not_allowed"):
            report(market,catalog,book,prior=p)

    def test_bad_catalog_not_downgraded_to_stale_warning(self):
        market,catalog,book,prior=sources()
        c=copy.deepcopy(catalog)
        c["listed_total"]+=1
        with self.assertRaises(ValueError):
            report(market,c,book,prior=prior)

    def test_actual_workflow_routes_only_offline_audit_and_no_live_promotion(self):
        text=Path(".github/workflows/v3-six-pillar-source-readiness.yml").read_text()
        self.assertIn("from v3_dart_offline_lag_plan import report",text)
        self.assertNotIn("from v3_dart_four_year_reference import plan",text)
        self.assertIn("fouryear-offline-plan.json",text)
        self.assertIn("v3_continuous_quality_watch.py",text)


if __name__=="__main__":
    unittest.main()
