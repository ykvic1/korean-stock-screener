"""Preserve archive gaps and distinguish waiting from replay readiness."""
from datetime import date, datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from v3_forward_horizon_readiness import HORIZONS
from v3_forward_observed_readiness import (
    build, horizon_readiness, sample_entry_sessions, share_review_coverage, top_ranked_issuers,
)
from v3_forward_share_actions import STATES


class ObservedReadinessTests(unittest.TestCase):
    def test_real_score_snapshots_share_an_entry_day_and_overlap(self):
        report = build(Path(__file__).resolve().parents[1],
                       datetime(2026, 11, 18, 3, tzinfo=timezone.utc))
        grouped = report['entry_session_sampling']
        duplicate = grouped['by_entry_session']['2026-11-18']
        self.assertEqual(duplicate['representative_score_asof'], '2026-11-17')
        self.assertEqual(duplicate['overlap_with_representative']['2026-11-16'],
                         {'top20': 14, 'top100': 81})
        self.assertFalse(report['cohorts']['2026-11-16']['entry_session_sample']
                         ['counts_toward_distinct_entry_sessions'])
        self.assertTrue(report['cohorts']['2026-11-17']['entry_session_sample']
                        ['counts_toward_distinct_entry_sessions'])

    def test_entry_session_counts_use_frozen_selection_even_if_alternate_has_prices(self):
        older, newer = '2026-11-16', '2026-11-17'
        ranks = {
            older: top_ranked_issuers({'A': ['이전 이름', 'KOSPI', 75, 1],
                                      'B': ['동일', 'KOSDAQ', 72, 2],
                                      'C': ['상위100', 'KOSPI', 65, 21]}),
            newer: top_ranked_issuers({'A': ['다른 이름', 'KOSPI', 85, 2],
                                      'B': ['동일', 'KOSDAQ', 78, 1],
                                      'C': ['상위100', 'KOSPI', 66, 22]}),
        }
        cohorts = {date: {'matched_opening_count': matched,
                          'horizons': {str(n): {'status': state} for n in HORIZONS}}
                   for date, matched, state in ((older, 3, 'daily_archive_ready_for_path_replay'),
                                                (newer, None, 'waiting_for_matched_entry'))}
        result = sample_entry_sessions(cohorts, {'2026-11-18': ranks})
        self.assertEqual(result['distinct_entry_session_count'], 1)
        self.assertEqual(result['matched_representative_entry_session_count'], 0)
        self.assertEqual(result['horizon_readiness_counts']['1'], {'waiting_for_matched_entry': 1})
        self.assertEqual(result['by_entry_session']['2026-11-18']['overlap_with_representative'][older],
                         {'top20': 1, 'top100': 2})

    def test_due_missing_session_cannot_be_hidden_by_later_archived_session(self):
        start, missing, target = (date(2026, 11, 16), date(2026, 11, 17), date(2026, 12, 16))
        archived = {start.isoformat(): {}, target.isoformat(): {}}
        now = datetime(2026, 12, 17, tzinfo=timezone.utc)
        with patch('v3_forward_observed_readiness.official_publish_time',
                   side_effect=lambda day: datetime.combine(day, datetime.min.time(), timezone.utc)):
            result = horizon_readiness(start, target, [start, missing, target], archived, now, True, {})
            self.assertEqual(result['status'], 'daily_archive_gaps')
            self.assertEqual(result['first_missing_due_session'], missing.isoformat())
            self.assertEqual(result['archived_session_count'], 2)
            archived[missing.isoformat()] = {}
            result = horizon_readiness(start, target, [start, missing, target], archived, now, True, {})
            self.assertEqual(result['status'], 'daily_archive_ready_for_path_replay')
            result = horizon_readiness(start, target, [start, missing, target], archived, now, False, {})
            self.assertEqual(result['status'], 'waiting_for_matched_entry')

    def test_closed_target_can_still_be_waiting_for_official_data(self):
        start, target = date(2026, 11, 16), date(2026, 12, 16)
        now = datetime(2026, 12, 16, 10, tzinfo=timezone.utc)
        with patch('v3_forward_observed_readiness.official_publish_time',
                   side_effect=lambda day: datetime(day.year, day.month, day.day, 23, tzinfo=timezone.utc)):
            result = horizon_readiness(start, target, [start, target], {}, now, True, {})
            self.assertEqual(result['status'], 'waiting_for_official_publication')
            self.assertEqual(result['due_session_count'], 1)
            result = horizon_readiness(start, target, [start], {}, now, True, {})
            self.assertEqual(result['status'], 'waiting_for_target_session')
            self.assertEqual(result['first_missing_due_session'], start.isoformat())

    def test_reconcile_real_entry_transition_and_missing_later_pair(self):
        with patch('v3_forward_observed_readiness.official_publish_time',
                   side_effect=lambda day: datetime.combine(day, datetime.min.time(), timezone.utc)):
            report = build(Path(__file__).resolve().parents[1], datetime(2026, 11, 17, tzinfo=timezone.utc))
        cohort = report['cohorts']['2026-11-13']
        self.assertEqual(cohort['ranked_entry_status_counts'], {
            'official_open_observed': 2471, 'not_traded': 101,
            'absent': 0, 'identity_unresolved': 0, 'open_unavailable': 0,
        })
        boundary = cohort['entry_share_action_review']
        self.assertEqual(boundary['previous_session_candidate'], '2026-11-13')
        self.assertTrue(boundary['source_linked'])
        self.assertGreater(boundary['market_transition_counts']['unchanged_share_count'], 0)
        self.assertFalse(report['full_corporate_action_history_certified'])

        days = [date(2026, 11, n) for n in (16, 17, 18)]
        counts = dict.fromkeys(STATES, 0)
        counts['change_needs_notice'] = 2
        review = share_review_coverage(days, {('2026-11-17', '2026-11-18'): counts})
        self.assertEqual(review['source_linked_review_count'], 1)
        self.assertEqual(review['first_missing_review_session'], '2026-11-17')
        self.assertEqual(review['unresolved_market_code_transitions_in_linked_reviews'], 2)
