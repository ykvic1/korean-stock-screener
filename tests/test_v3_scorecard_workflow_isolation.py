"""No-network regression for isolated, source-only scorecard publisher."""
from pathlib import Path
import unittest

WORKFLOW=(Path(__file__).resolve().parents[1] /
          ".github/workflows/v3-all-listed-scorecard.yml")


class ScorecardWorkflowPublicationTests(unittest.TestCase):
    def test_scorecard_is_isolated_from_high_priority_source_queue(self):
        text=WORKFLOW.read_text(encoding="utf8")
        self.assertIn("group: korea-stock-screener-scorecard-publisher",text)
        self.assertNotIn("group: korea-stock-screener-publication",text)
        self.assertIn("cancel-in-progress: false",text)
        self.assertIn("cron: '24,54 * * * *'",text)
        self.assertIn("python v3_all_listed_scorecard.py --mode write",text)

    def test_publisher_never_writes_an_independently_owned_source(self):
        text=WORKFLOW.read_text(encoding="utf8")
        self.assertNotIn("python v3_dart_industry_earnings_sample.py --mode write",text)
        # Industry earnings, market, DART sources and V2.2 scans retain
        # their separate, original writers.
        for path in ("docs/v3-dart-industry-earnings-sample.json",
                     "docs/v3-dart-industry-classification.json",
                     "docs/v3-dart-bulk-history.json",
                     "docs/v3-dart-cashflow-reference.json",
                     "docs/data.json", "docs/v22-market-catalog.json"):
            self.assertNotIn("git add "+path,text)
        self.assertIn("git add docs/v3-all-listed-scores.csv "
                      "docs/v3-all-listed-scorecard-summary.json",text)

    def test_bounded_rebase_recompute_verify_before_nonforce_publish(self):
        text=WORKFLOW.read_text(encoding="utf8")
        block=text.split("for attempt in 1 2 3; do",1)[1]
        for action in ("git pull --rebase origin main",
                       "python v3_all_listed_scorecard.py --mode write",
                       "python v3_all_listed_scorecard.py --mode verify",
                       "git push origin HEAD:main"):
            self.assertIn(action,block)
        self.assertLess(block.index("git pull --rebase origin main"),
                        block.index("python v3_all_listed_scorecard.py --mode write"))
        self.assertLess(block.index("python v3_all_listed_scorecard.py --mode verify"),
                        block.index("git push origin HEAD:main"))
        self.assertIn("scorecard_publish_retries_exhausted",block)
        self.assertNotIn("git push --force",text)
        self.assertNotIn("DART_API_KEY:",text)
        self.assertNotIn("KRX_API_KEY:",text)


if __name__=="__main__":
    unittest.main()
