"""Guard against two publishers writing docs/ at the same time."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATHS=[
    ROOT/'.github/workflows/daily.yml',
    ROOT/'.github/workflows/quant.yml',
    ROOT/'.github/workflows/reliability-v22.yml',
]


class PublicationGuardTests(unittest.TestCase):
    def test_all_publication_workflows_share_the_same_concurrency_lock(self):
        for f in PATHS:
            s=f.read_text(encoding='utf-8')
            self.assertIn('group: korea-stock-screener-publication',s,f.name)
            self.assertIn('cancel-in-progress: false',s,f.name)

    def test_daily_is_based_on_latest_main_even_for_dispatch(self):
        s=PATHS[0].read_text(encoding='utf-8')
        self.assertIn('uses: actions/checkout@v4\n        with:\n          ref: main',s)

    def test_artifacts_saved_on_workflow_failure(self):
        daily=PATHS[0].read_text(encoding='utf-8')
        self.assertIn('if: always()\n        uses: actions/upload-artifact@v4',daily)
        for f in PATHS:
            self.assertIn('if-no-files-found: warn',f.read_text(encoding='utf-8'),f.name)

    def test_missing_original_cohort_files_trigger_audit(self):
        s=PATHS[2].read_text(encoding='utf-8')
        for p in ('baseline_cohort.py','config/v22_baseline_53.json','tests/test_baseline_cohort.py'):
            self.assertIn(p,s)

if __name__=='__main__':
    unittest.main()
