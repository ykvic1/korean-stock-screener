"""Preserve archive gaps and distinguish waiting from replay readiness."""
from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from v3_forward_observed_readiness import horizon_readiness


class ObservedReadinessTests(unittest.TestCase):
    def test_due_missing_session_cannot_be_hidden_by_later_archived_session(self):
        start, missing, target = (date(2026, 11, 16), date(2026, 11, 17), date(2026, 12, 16))
        archived = {start.isoformat(): {}, target.isoformat(): {}}
        now = datetime(2026, 12, 17, tzinfo=timezone.utc)
        with patch('v3_forward_observed_readiness.official_publish_time',
                   side_effect=lambda day: datetime.combine(day, datetime.min.time(), timezone.utc)):
            result = horizon_readiness(start, target, [start, missing, target], archived, now, True)
            self.assertEqual(result['status'], 'daily_archive_gaps')
            self.assertEqual(result['first_missing_due_session'], missing.isoformat())
            self.assertEqual(result['archived_session_count'], 2)
            archived[missing.isoformat()] = {}
            result = horizon_readiness(start, target, [start, missing, target], archived, now, True)
            self.assertEqual(result['status'], 'daily_archive_ready_for_path_replay')
            result = horizon_readiness(start, target, [start, missing, target], archived, now, False)
            self.assertEqual(result['status'], 'waiting_for_matched_entry')

    def test_closed_target_can_still_be_waiting_for_official_data(self):
        start, target = date(2026, 11, 16), date(2026, 12, 16)
        now = datetime(2026, 12, 16, 10, tzinfo=timezone.utc)
        with patch('v3_forward_observed_readiness.official_publish_time',
                   side_effect=lambda day: datetime(day.year, day.month, day.day, 23, tzinfo=timezone.utc)):
            result = horizon_readiness(start, target, [start, target], {}, now, True)
            self.assertEqual(result['status'], 'waiting_for_official_publication')
            self.assertEqual(result['due_session_count'], 1)
            result = horizon_readiness(start, target, [start], {}, now, True)
            self.assertEqual(result['status'], 'waiting_for_target_session')
            self.assertEqual(result['first_missing_due_session'], start.isoformat())
