"""No-network contract for immediate observed-source -> R1d replay handoff."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OBSERVED = ROOT / ".github/workflows/v3-current-observed-scorecard.yml"
OPERATIONAL = ROOT / ".github/workflows/v3-operational-research-score.yml"


class ObservedPublicationDispatchTests(unittest.TestCase):
    def test_dispatch_follows_verified_nonforce_publication_only(self):
        source = OBSERVED.read_text(encoding="utf8")
        publication = source.split("      - name: Publish current-day CSV", 1)[1]
        dispatch = publication.split("      - name: Recalculate R1d immediately", 1)
        self.assertEqual(len(dispatch), 2)
        writer, downstream = dispatch
        self.assertIn("id: publication", writer)
        self.assertIn("python v3_current_observed_scorecard.py --mode verify", writer)
        self.assertIn("python v3_near_100_observed_gap.py --mode verify", writer)
        self.assertIn("if git push origin HEAD:main; then", writer)
        self.assertIn('echo "changed=true" >> "$GITHUB_OUTPUT"', writer)
        self.assertLess(writer.index("python v3_near_100_observed_gap.py --mode verify"),
                        writer.index("if git push origin HEAD:main; then"))
        self.assertIn("if: steps.publication.outputs.changed == 'true'", downstream)
        self.assertIn('gh workflow run v3-operational-research-score.yml', downstream)
        self.assertIn('GH_TOKEN: ${{ github.token }}', downstream)

    def test_dispatch_has_bounded_access_and_no_event_loop(self):
        source = OBSERVED.read_text(encoding="utf8")
        operational = OPERATIONAL.read_text(encoding="utf8")
        self.assertIn("permissions:\n  contents: write\n", source)
        self.assertIn("  actions: write", source)
        self.assertIn("  workflow_dispatch:", operational)
        self.assertIn("group: v3-operational-research-score-publisher", operational)
        self.assertNotIn("docs/v3-operational-research-scores.json", source.split("  push:", 1)[1].split("  schedule:", 1)[0])
        self.assertNotIn("git push --force", source)


if __name__ == "__main__":
    unittest.main()
