"""Only scoring-code changes launch an immediate whole-market refresh."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ImmediateFullMarketRefreshTests(unittest.TestCase):
    def test_scoring_push_replays_full_market_without_recursive_data_push(self):
        workflow = (ROOT / ".github/workflows/full-market-batches.yml").read_text(
            encoding="utf-8")
        before_dispatch = workflow.split("  workflow_dispatch:", 1)[0]
        self.assertIn("  schedule:", before_dispatch)
        self.assertIn("cron: '30 9 * * 1-5'", before_dispatch)
        self.assertIn("  push:", before_dispatch)
        self.assertIn("branches: [main]", before_dispatch)
        for source in ("full_market_batch.py", "screener.py",
                       "v3_ma240_cross_research.py",
                       "v3_pattern_composite_score.py",
                       ".github/workflows/full-market-batches.yml"):
            self.assertIn(f"      - {source}", before_dispatch)
        self.assertNotIn("      - docs/data.json", before_dispatch)
        self.assertNotIn("      - docs/", before_dispatch)
        self.assertIn(
            "MODE: ${{ github.event_name != 'workflow_dispatch' && 'publish' || inputs.mode }}",
            workflow)
        self.assertEqual(workflow.count(
            "github.event_name != 'workflow_dispatch' || inputs.mode == 'publish'"),
            4)
        self.assertIn("options: [audit, publish]", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("max-parallel: 2", workflow)
        self.assertIn("python full_market_batch.py merge", workflow)


if __name__ == "__main__":
    unittest.main()
