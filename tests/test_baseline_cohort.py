import json
import tempfile
import unittest
from pathlib import Path
from baseline_cohort import load_cohort, union_stocks, cohort_summary, git_blob_sha


class BaselineCohortTests(unittest.TestCase):
    def test_original_53_are_stable_and_unique(self):
        cohort = load_cohort()
        self.assertEqual(len(cohort), 53)
        self.assertEqual(len({x['code'] for x in cohort}), 53)

    def test_rotating_candidates_cannot_drop_original_failures(self):
        baseline = load_cohort()
        current = [{'code': '999999', 'name': 'new candidate'}, baseline[0]]
        combined = union_stocks(current, baseline)
        self.assertEqual(len(combined), 54)
        self.assertTrue({s['code'] for s in baseline} <= {s['code'] for s in combined})

    def test_incomplete_or_duplicate_cohort_cannot_produce_recovery_rate(self):
        baseline = load_cohort()
        for rows in (baseline[:-1], baseline[:-1] + [baseline[0]]):
            with self.assertRaises(ValueError):
                cohort_summary(rows)

    def test_segment_is_not_full_history_or_session_validation(self):
        rows = [dict(s, rows=1000, strict_valid=True) for s in load_cohort()]
        result = cohort_summary(rows)
        self.assertEqual(result['numeric_segment_usable'], 53)
        self.assertEqual(result['full_history_verified'], 0)
        self.assertEqual(result['regular_session_verified'], 0)

    def test_tampered_source_path_or_blob_id_are_rejected(self):
        manifest = json.loads(Path('config/v22_baseline_53.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'config' / 'manifest.json'
            out.parent.mkdir()
            for key, bad in [('source','docs/quant.json'),('source_git_blob_sha','0'*40)]:
                changed = dict(manifest, **{key:bad})
                out.write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_cohort(out)

    def test_git_object_sha_is_not_plain_file_sha(self):
        # Well-known Git blob hash for b'hello\n'.
        self.assertEqual(git_blob_sha(b'hello\n'), 'ce013625030ba8dba906f756967f9e9ca394464a')


if __name__ == '__main__':
    unittest.main()
