import unittest
from baseline_cohort import load_cohort, union_stocks, cohort_summary


class BaselineCohortTests(unittest.TestCase):
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
