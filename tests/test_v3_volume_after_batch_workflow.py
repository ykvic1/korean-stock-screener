"""Workflow source guards for same-close volume and MA-cross replay."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class VolumePostBatchReplayTests(unittest.TestCase):
    def test_successful_main_full_market_run_replays_volume_research(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "v3-volume-observed.yml").read_text(encoding="utf-8")
        upstream = (ROOT / ".github" / "workflows" /
                    "full-market-batches.yml").read_text(encoding="utf-8")

        self.assertIn("name: Full-Market 100-Stock Batches", upstream)
        self.assertIn("workflow_run:", workflow)
        self.assertIn("- Full-Market 100-Stock Batches", workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",
                      workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",
                      workflow)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository",
                      workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("python v3_volume_participation.py --mode write", workflow)
        self.assertIn("python v3_volume_participation.py --mode verify", workflow)
        self.assertIn("cron: '12,42 * * * *'", workflow)


if __name__ == "__main__":
    unittest.main()
