"""After-close full-market refresh without code-push duplicate API scans."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ImmediateFullMarketRefreshTests(unittest.TestCase):
    def test_after_close_collection_does_not_refetch_on_code_or_data_push(self):
        workflow = (ROOT / ".github/workflows/full-market-batches.yml").read_text(
            encoding="utf-8")
        before_dispatch = workflow.split("  workflow_dispatch:", 1)[0]
        self.assertIn("  schedule:", before_dispatch)
        self.assertIn("cron: '30 17 * * 1-5'", before_dispatch)
        self.assertNotIn("  push:", before_dispatch)
        self.assertIn(
            "MODE: ${{ github.event_name == 'schedule' && 'publish' || inputs.mode }}",
            workflow)
        self.assertEqual(workflow.count(
            "github.event_name == 'schedule' || inputs.mode == 'publish'"),
            5)
        self.assertEqual(workflow.count("v3_aftermarket_cutoff.py --manifest"), 2)
        self.assertIn("options: [audit, publish]", workflow)
        self.assertIn("default: audit", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("max-parallel: 2", workflow)
        self.assertIn("python full_market_batch.py merge", workflow)
        self.assertNotIn("python -m unittest discover -s tests", workflow)


if __name__ == "__main__":
    unittest.main()
