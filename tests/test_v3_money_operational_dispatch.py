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

    def test_current_score_push_cannot_cancel_shared_actual_money_publisher(self):
        current=(Path(__file__).resolve().parents[1]/
            '.github/workflows/v3-current-observed-scorecard.yml').read_text()
        source_header=current.split('\njobs:',1)[0]
        self.assertIn('group: korea-stock-screener-current-observed-publisher',source_header)
        self.assertIn('queue: max',source_header)
        self.assertIn('cancel-in-progress: false',source_header)
        self.assertNotIn("cancel-in-progress: ${{ github.event_name == 'push' }}",source_header)
        self.assertIn('group: korea-stock-screener-current-observed-publisher',self.live)
        self.assertIn('queue: max',self.live)
        self.assertIn('cancel-in-progress: false',self.live)

    def test_write_permissions_and_pending_queue_are_restricted_to_trusted_live_job(self):
        offline_job=self.offline.split('  offline:',1)[1]
        self.assertIn('permissions:\n      contents: read',offline_job)
        self.assertNotIn('actions: write',offline_job)
        self.assertIn('permissions:\n      contents: write\n      actions: write',self.live)
        self.assertIn("github.event_name != 'pull_request'",self.live)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",self.live)
        self.assertIn('github.event.workflow_run.head_repository.full_name == github.repository',self.live)
        self.assertIn('queue: max',self.live)

    def test_fcf_evidence_replayed_and_staged_with_current_scores(self):
        publication=self.live.split('id: publication',1)[1].split(
            'Refresh operational scores explicitly',1)[0]
        initial,retry=publication.split('for attempt in 1 2 3; do',1)
        for block in (initial,retry):
            preview='v3_dart_cashflow_factor_preview.py --mode write --defer-source-lag'
            gap='v3_dart_cashflow_gap_audit.py --mode write --defer-source-lag'
            score='v3_current_observed_scorecard.py --mode write --refresh-derived'
            self.assertLess(block.index(preview),block.index(gap))
            self.assertLess(block.index(gap),block.index(score))
            staged=next(line for line in block.splitlines() if 'git add --' in line)
            for path in ('docs/v3-dart-fcf-research-preview.json',
                         'docs/v3-dart-cashflow-gap-summary.json',
                         'docs/v3-dart-cashflow-issuer-evidence.json',
                         'docs/v3-current-observed-all-listed-summary.json'):
                self.assertIn(path,staged)
        for script in ('v3_dart_cashflow_factor_preview.py','v3_dart_cashflow_gap_audit.py'):
            self.assertLess(retry.index(script+' --mode verify --defer-source-lag'),
                            retry.index('git push origin HEAD:main'))


if __name__=='__main__':unittest.main()
