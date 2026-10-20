"""Guard capped automatic 4-year DART and manual dry-run; preserve API budget."""
from pathlib import Path
import re
import unittest


class FourYearRollingWorkflowSafety(unittest.TestCase):
    def test_scheduled_adaptive_cap_and_manual_dry_run_default(self):
        wf=Path(".github/workflows/v3-fouryear-dart-financial-reference.yml").read_text(encoding="utf8")
        annual=Path(".github/workflows/v3-rolling-dart-annual-book.yml").read_text(encoding="utf8")
        source=Path("v3_dart_four_year_reference.py").read_text(encoding="utf8")
        provider=Path("official_data.py").read_text(encoding="utf8")
        self.assertIn("workflow_dispatch:",wf)
        self.assertIn("schedule:",wf)
        self.assertIn("43 17 * * 0-4",wf)
        self.assertEqual(len(re.findall(r"^\s+- cron:",wf,flags=re.M)),1)
        self.assertIn("automatic_daily_limit(prior)",wf)
        self.assertIn("AUTO_BATCH:",wf)
        self.assertIn("four['requested_this_run']>12",wf)
        self.assertNotIn("push:",wf)
        self.assertIn("default: 'dry-run'",wf)
        self.assertIn("options: ['dry-run', 'live']",wf)
        self.assertIn("options: ['1', '2', '4']",wf)
        self.assertIn("MAX_COMPANIES = 12",source)
        self.assertIn("MIN_HEALTHY_FRACTION = .75",source)
        self.assertIn("years=4,budget_seconds=budget",source)
        self.assertIn("budget=min(900,max(420,150*limit))",source)
        self.assertIn("inputs.mode == 'live'",wf)
        self.assertIn("if os.environ['REQUESTED_MODE']=='live':",wf)
        self.assertIn("if: ${{ github.event_name == 'schedule' || inputs.mode == 'live' }}",wf)
        self.assertIn("stale_fouryear_market_snapshot",wf)
        self.assertIn("stale_rebased_fouryear_not_publishable",wf)
        self.assertIn("group: korea-stock-screener-publication",wf)
        self.assertIn("MAX_PER_RUN=40",Path("v3_dart_rolling_book_reference.py").read_text(encoding="utf8"))
        self.assertEqual(len(re.findall(r"^\s+- cron:",annual,flags=re.M)),8)
        self.assertIn("collector=dart_collect",Path("v3_dart_rolling_book_reference.py").read_text(encoding="utf8"))
        self.assertIn("budget_seconds=300",Path("v3_dart_rolling_book_reference.py").read_text(encoding="utf8"))
        self.assertIn("years=3, budget_seconds=360",provider)
        self.assertIn("1 <= years <= 4",provider)
        self.assertIn("stock_buy_certified",annual)
        self.assertNotIn("v3_dart_four_year_reference.py",annual)
        self.assertNotIn("docs/data.json\n          git add",wf)
        self.assertNotIn("git add docs/index.html",wf)


if __name__=="__main__":
    unittest.main()
