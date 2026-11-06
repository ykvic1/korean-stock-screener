"""Pages outage recovery is independent of score mutation; no network calls."""
import unittest
from pathlib import Path
from v3_pages_deployment_retry import needs_retry


def run(conclusion='failure', status='completed'):
    return {'name':'pages build and deployment', 'status':status, 'conclusion':conclusion}


class PagesRetryTests(unittest.TestCase):
    def test_failed_completed_deployment_needs_retry_without_score_changes(self):
        for reason in ('failure','cancelled','timed_out'):
            self.assertTrue(needs_retry({'workflow_runs':[run(reason),run('success')]}))

    def test_latest_success_does_not_repeat_an_old_failure(self):
        self.assertFalse(needs_retry({'workflow_runs':[run('success'),run()]}))
        self.assertFalse(needs_retry({'workflow_runs':[]}))
        self.assertFalse(needs_retry({'workflow_runs':[run('action_required')]}))

    def test_older_active_deployment_is_not_canceled_by_a_newer_canceled_run(self):
        for status in ('queued','in_progress','waiting','requested','pending'):
            self.assertFalse(needs_retry({'workflow_runs':[run('cancelled'),run(None,status)]}))

    def test_unknown_or_mixed_workflow_metadata_cannot_authorize_retry(self):
        for payload in ({}, {'workflow_runs':None}, {'workflow_runs':[{}]},
                        {'workflow_runs':[dict(run(),name='some other workflow')]},
                        {'workflow_runs':[dict(run(),conclusion=None)]}):
            with self.assertRaises(ValueError):needs_retry(payload)

    def test_real_publisher_retains_no_change_recovery_and_active_run_guard(self):
        s=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-operational-research-score.yml').read_text()
        self.assertIn('actions: read',s)
        self.assertIn("steps.publish_scores.outputs.changed != 'true'",s)
        self.assertIn("steps.pages_recovery.outputs.retry == 'true'",s)
        self.assertIn('python v3_pages_deployment_retry.py --runs',s)
        self.assertIn('per_page=100',s)
        self.assertIn('dynamic/pages/pages-build-deployment',s)
        self.assertLess(s.index('id: publish_scores'),s.index('id: pages_recovery'))
