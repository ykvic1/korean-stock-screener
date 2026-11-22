"""Keep the immutable 490/490 historical collector from running on a timer."""

import json
from pathlib import Path
import re
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]


class CompletedDerivedHistoryWorkflowTests(TestCase):
    def test_completed_fixed_history_retains_manual_reaudit_without_scheduled_reruns(self):
        progress = json.loads((ROOT / "docs/v3-krx-derived-backfill-progress.json")
                              .read_text(encoding="utf-8"))
        workflow = (ROOT / ".github/workflows/v3-krx-derived-history.yml").read_text(
            encoding="utf-8")
        self.assertEqual(progress["five_session_cohorts_target"], 490)
        self.assertEqual(progress["five_session_cohorts_completed"], 490)
        self.assertEqual(progress["five_session_cohorts_remaining"], 0)
        self.assertEqual(progress["source_candidate_end"], "2026-10-16")
        self.assertRegex(workflow, r"(?m)^  workflow_dispatch:\s*$")
        self.assertRegex(workflow, r"(?m)^  push:\s*$")
        self.assertNotRegex(workflow, r"(?m)^  schedule:\s*$")
        self.assertIn("python v3_krx_derived_backfill.py", workflow)
