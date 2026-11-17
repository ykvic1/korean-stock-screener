"""Keep publication followups independent of nested workflow_run limits."""

from pathlib import Path
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


class PostPublicationReplayTests(unittest.TestCase):
    def test_full_market_dispatches_each_followup_after_publication(self):
        market = (WORKFLOWS / "full-market-batches.yml").read_text()
        publish = market.split("id: publish", 1)[1].split("dispatch-close-followups:", 1)[0]
        self.assertLess(publish.index("git push origin HEAD:main"),
                        publish.index('echo "published=true"'))
        dispatch = market.split("dispatch-close-followups:", 1)[1]
        self.assertIn("needs: combine", dispatch)
        self.assertIn("if: needs.combine.outputs.published == 'true'", dispatch)
        self.assertIn("fail-fast: false", dispatch)
        self.assertIn('gh workflow run "$FOLLOWUP" --repo "$GITHUB_REPOSITORY" --ref main', dispatch)
        for name in ("v3-rolling-dart-annual-book.yml", "v3-regular-close-indicators.yml",
                     "v3-current-price-replay.yml"):
            with self.subTest(workflow=name):
                self.assertIn(f"- workflow: {name}", dispatch)
                target = (WORKFLOWS / name).read_text()
                self.assertIn("workflow_dispatch:", target)
                self.assertNotIn("Full-Market 100-Stock Batches", target)
        self.assertIn("workflow: v3-current-price-replay.yml\n            mode: write", dispatch)

    def test_missing_score_capture_retries_on_an_aligned_unchanged_replay(self):
        scores = (WORKFLOWS / "v3-operational-research-score.yml").read_text()
        capture = scores.split("Start first forward capture for the aligned score date", 1)[1]
        self.assertIn("steps.publish_scores.outcome == 'success'", capture)
        self.assertIn("steps.recompute.outputs.waiting != 'true'", capture)
        self.assertIn("steps.publish_scores.outputs.waiting != 'true'", capture)
        self.assertNotIn("steps.publish_scores.outputs.changed", capture)
        self.assertLess(capture.index('ops/forward-score-observations/$day.json'),
                        capture.index("gh workflow run v3-score-observation-capture.yml"))
        target = (WORKFLOWS / "v3-score-observation-capture.yml").read_text()
        self.assertIn("workflow_dispatch:", target)
        self.assertNotIn("workflow_run:", target)

    def test_legacy_scorecard_publication_triggers_read_only_watch(self):
        scorecard = (WORKFLOWS / "v3-all-listed-scorecard.yml").read_text()
        self.assertLess(scorecard.index("git push origin HEAD:main"),
                        scorecard.index('echo "changed=true"'))
        audit = scorecard.split("Recheck release blockers on the updated publication", 1)[1]
        self.assertIn("if: steps.publication.outputs.changed == 'true'", audit)
        self.assertIn("gh workflow run v3-v30b-readiness-audit.yml", audit)
        watch = (WORKFLOWS / "v3-v30b-readiness-audit.yml").read_text()
        self.assertIn("workflow_dispatch:", watch)
        self.assertIn("contents: read", watch)
        self.assertNotIn("contents: write", watch)
