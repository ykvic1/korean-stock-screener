"""Bounded money backfill workflow contract (no live KRX calls)."""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v3-krx-money-history.yml"


class MoneyFastLaneWorkflowTests(unittest.TestCase):
    def test_normal_schedule_keeps_original_single_batch_budget(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- cron: '49 */2 * * *'", source)
        self.assertIn("default: '16'", source)
        self.assertIn("default: '1'", source)
        self.assertIn("options: ['1', '14']", source)
        self.assertIn("inputs.max_windows || '16'", source)
        self.assertIn("inputs.max_segments || '1'", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("timeout-minutes: 120", source)

    def test_catchup_is_single_explicit_commit_and_bounded_checkpoint_loop(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        step = source.split(
            "- name: Resume bounded actual-money cohorts through private verified checkpoints", 1
        )[1]
        self.assertIn("feat(v3.0b): money history fast lane", step)
        self.assertIn("segment<=V3_MONEY_BATCH_SEGMENTS", step)
        self.assertIn("result=\"$(python v3_krx_money_backfill.py)\"", step)
        self.assertIn('if [[ "$remaining" == \'0\' ]]', step)
        self.assertIn('SECONDS >= 6000', step)
        self.assertIn('2,240 official KRX market-day calls', step)
        self.assertNotIn("contents: write", source)
        script = step.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines()) + "\n"
        check = subprocess.run(["bash", "-n"], input=script, text=True,
                               capture_output=True, check=False)
        self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == "__main__":
    unittest.main()
