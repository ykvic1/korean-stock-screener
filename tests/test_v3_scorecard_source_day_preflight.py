"""Pure offline tests of market-day-aware V3 combined scorecard publishing."""
import copy
from datetime import date,timedelta
from pathlib import Path
import unittest

from v3_scorecard_source_day_preflight import (
    preflight_day,ScorecardReadinessError,
)
from v3_dart_bulk_history import SCHEMA as BULK, SCORE_SCHEMA as PARTS
from v3_weekly_rotation import SCHEMA as WEEKLY

ROOT=Path(__file__).resolve().parents[1]
DAY="2026-10-21"


def sources():
    return (
        {"schema":BULK,"asof":DAY,"historical_PIT_source_certified":False,
         "raw_source_rows_published":False,"fiscal_years":[2022,2023,2024,2025]},
        {"schema":PARTS,"asof":DAY,"all_scores_provisional_unverified":True,
         "fully_verified_six_pillar_count":0},
        {"schema":WEEKLY,"market_asof":DAY,
         "six_pillar_full_score_confirmed":False},
    )


class ScorecardMarketDayGateTests(unittest.TestCase):
    def test_same_day_source_permits_only_further_full_ledger_audit(self):
        state=preflight_day(DAY,*sources())
        self.assertTrue(state["ready"])
        self.assertEqual(state["lagging_sources"],[])
        self.assertFalse(state["official_100_point_scores_certified"])

    def test_only_bounded_genuine_source_lag_is_deferred(self):
        h,p,w=sources()
        h["asof"]="2026-10-20"
        p["asof"]="2026-10-20"
        w["market_asof"]="2026-10-20"
        status=preflight_day(DAY,h,p,w)
        self.assertFalse(status["ready"])
        self.assertEqual(status["lagging_sources"],
                         ["dart_annual","dart_factors","rotating_technical"])
        self.assertEqual(status["new_provider_calls"],0)

    def test_fraudulent_or_incompatible_reference_is_error_not_deferred(self):
        for field in ("future","too_old","wrong_pit","wrong_schema","wrong_years"):
            h,p,w=sources()
            if field=="future": h["asof"]="2026-10-22"
            if field=="too_old": p["asof"]="2026-10-01"
            if field=="wrong_pit": h["historical_PIT_source_certified"]=True
            if field=="wrong_schema": w["schema"]="spoofed"
            if field=="wrong_years":h["fiscal_years"]=[2021,2022,2023,2024]
            with self.assertRaises(ScorecardReadinessError, msg=field):
                preflight_day(DAY,h,p,w)

    def test_workflow_skips_calculate_publish_on_source_mismatch(self):
        wf=(ROOT/".github/workflows/v3-all-listed-scorecard.yml").read_text(encoding="utf8")
        self.assertIn("id: date_gate",wf)
        self.assertIn("python v3_scorecard_source_day_preflight.py",wf)
        self.assertGreaterEqual(wf.count("steps.date_gate.outputs.ready == 'true'"),3)
        self.assertIn("DAY_READY:",wf)
        self.assertIn("group: korea-stock-screener-scorecard-publisher",wf)
        self.assertNotIn("DART_API_KEY:",wf)
        self.assertNotIn("KRX_API_KEY:",wf)
        self.assertNotIn("git push --force",wf)

    def test_publisher_rechecks_market_and_source_dates_after_every_rebase(self):
        wf=(ROOT/".github/workflows/v3-all-listed-scorecard.yml").read_text(encoding="utf8")
        publish=wf.split("for attempt in 1 2 3; do",1)[1]
        self.assertLess(publish.index("git pull --rebase origin main"),
                        publish.index('REBASE_READY="$(python v3_scorecard_source_day_preflight.py'))
        self.assertLess(publish.index('REBASE_READY="$(python v3_scorecard_source_day_preflight.py'),
                        publish.index("python v3_all_listed_scorecard.py --mode write"))
        self.assertLess(publish.index("python v3_all_listed_scorecard.py --mode verify"),
                        publish.index("git push origin HEAD:main"))
        self.assertIn('if [[ "$REBASE_READY" == \'false\' ]]; then',publish)
        self.assertIn("scorecard_publish_deferred_after_rebase",publish)
        self.assertIn("exit 0",publish.split("scorecard_publish_deferred_after_rebase",1)[1].split("fi",1)[0])
        # Script-level fail-closed errors MUST propagate out of the pipeline;
        # only an actual boolean false is a routine defer state.
        self.assertIn("set -euo pipefail",wf)
        self.assertIn('assert type(x) is bool',publish)


if __name__=="__main__":
    unittest.main()
