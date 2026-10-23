"""Policy regression: source completion triggers a safe, bounded private bridge."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SOURCE = WORKFLOWS / "v3-krx-derived-history.yml"
BRIDGE = WORKFLOWS / "v3-krx-derived-boundaries.yml"


def read(path):
    return path.read_text(encoding="utf-8")


class SourceCompletionBoundaryPolicy(unittest.TestCase):
    def test_successful_backfill_is_only_event_source(self):
        source = read(SOURCE)
        bridge = read(BRIDGE)
        self.assertIn(
            "name: V3 KRX historical derived-only backfill", source
        )
        self.assertRegex(
            bridge,
            r"(?m)^  workflow_run:\n"
            r"    workflows:\n"
            r"      - V3 KRX historical derived-only backfill\n"
            r"    types: \[completed\]$",
        )

    def test_untrusted_and_failed_runs_do_not_use_api(self):
        bridge = read(BRIDGE)
        self.assertIn("github.event_name == 'workflow_run'", bridge)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", bridge)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", bridge)
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            bridge,
        )
        self.assertIn(
            "github.event_name == 'workflow_dispatch' && inputs.mode == 'live'",
            bridge,
        )
        self.assertNotIn("github.event_name == 'workflow_run' ||", bridge)

    def test_private_serial_execution_with_fallback_and_read_only_repo(self):
        bridge = read(BRIDGE)
        source = read(SOURCE)
        self.assertIn("group: v3-krx-private-derived-history", bridge)
        self.assertIn("group: v3-krx-private-derived-history", source)
        for workflow in (bridge, source):
            self.assertIn("cancel-in-progress: false", workflow)
            self.assertIn("contents: read", workflow)
            self.assertNotIn("contents: write", workflow)
            self.assertIn("persist-credentials: false", workflow)
            self.assertIn("V3_PRIVATE_ARCHIVE_REPO:", workflow)
            self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", workflow)
        self.assertIn("github.event_name == 'schedule'", bridge)
        self.assertEqual(
            re.findall(r"^\s+- cron: '([^']+)'", bridge, re.MULTILINE),
            ["47 17 * * *", "47 22 * * *", "47 2 * * *",
             "47 5 * * *", "47 13 * * *"],
        )
        self.assertIn("options: [dry_run, live]", bridge)
        self.assertIn("default: dry_run", bridge)

    def test_live_calls_remain_per_run_capped_and_day_capacity_conservative(self):
        source_script = read(ROOT / "v3_krx_derived_backfill.py")
        bridge_script = read(ROOT / "v3_krx_derived_bridges.py")
        self.assertIn("MAX_WINDOWS_PER_RUN=16", source_script)
        self.assertIn("MAX_SOURCE_CALLS=MAX_WINDOWS_PER_RUN*10", source_script)
        self.assertIn("MAX_LINKS_PER_RUN=24", bridge_script)
        # Conservative hypothetical: all 5 schedules PLUS 5 completion
        # events use their entire bridge caps. Normal resume should use fewer.
        source_calls = 5 * 160
        bridge_calls = (5 + 5) * 96
        self.assertEqual(source_calls + bridge_calls, 1760)
        self.assertLess(source_calls + bridge_calls, 10000)

    def test_production_market_screen_is_not_changed(self):
        production = read(WORKFLOWS / "full-market-batches.yml")
        self.assertIn("cron: '30 9 * * 1-5'", production)
        bridge = read(BRIDGE)
        self.assertNotIn("full-market-batches", bridge)
        self.assertNotIn("docs/", bridge)


if __name__ == "__main__":
    unittest.main()
