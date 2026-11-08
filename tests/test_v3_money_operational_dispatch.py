"""Publication-order and permission regression for saved money -> operations."""
from pathlib import Path
import unittest


class OperationalDispatchTests(unittest.TestCase):
    def setUp(self):
        self.text=(Path(__file__).resolve().parents[1]/
            '.github/workflows/v3-actual-money-score.yml').read_text()
        self.offline,self.live=self.text.split('  publish:',1)
        self.dispatch=self.live.split(
            '      - name: Refresh operational scores explicitly',1)[1]

    def test_only_successfully_published_ready_sources_dispatch_saved_score_replay(self):
        self.assertIn("if: steps.projection.outputs.ready == 'true' && steps.publication.conclusion == 'success'",
            self.dispatch)
        self.assertIn('id: publication',self.live)
        self.assertIn('v3-operational-research-score.yml/dispatches',self.dispatch)
        self.assertIn('-f ref=main',self.dispatch)
        self.assertNotIn('KRX_API_KEY',self.dispatch)
        self.assertNotIn('DART_API_KEY',self.dispatch)

    def test_dispatch_occurs_after_private_cleanup_and_verified_publication(self):
        self.assertLess(self.live.index('Remove private snapshot before any public staging'),
            self.live.index('id: publication'))
        self.assertLess(self.live.index('git push origin HEAD:main'),
            self.live.index('Refresh operational scores explicitly'))
        self.assertIn('GH_TOKEN: ${{ github.token }}',self.dispatch)
        self.assertNotIn('V3_PRIVATE_ARCHIVE_TOKEN',self.dispatch)

    def test_write_permissions_and_pending_queue_are_restricted_to_trusted_live_job(self):
        offline_job=self.offline.split('  offline:',1)[1]
        self.assertIn('permissions:\n      contents: read',offline_job)
        self.assertNotIn('actions: write',offline_job)
        self.assertIn('permissions:\n      contents: write\n      actions: write',self.live)
        self.assertIn("github.event_name != 'pull_request'",self.live)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",self.live)
        self.assertIn('github.event.workflow_run.head_repository.full_name == github.repository',self.live)
        self.assertIn('queue: max',self.live)


if __name__=='__main__':unittest.main()
