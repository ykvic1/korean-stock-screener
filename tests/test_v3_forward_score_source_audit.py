"""Keep frozen forward source reconciliation strict across revisions."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unittest

from v3_forward_score_source_audit import verify_observation
from v3_score_observation_capture import capture


class ForwardScoreSourceAuditTest(unittest.TestCase):
    def setUp(self):
        self.capture_time = datetime(2026, 11, 19, 8, tzinfo=timezone.utc)
        day = self.capture_time.date().isoformat()
        self.market = json.dumps({'date': day, 'stocks': []}).encode()
        self.score = json.dumps({
            'version': 'v3-saved-model', 'asof': day, 'listed_total': 1,
            'investment_score_certified': False, 'performance_validated': False,
            'source_hashes': {'data.json': sha256(self.market).hexdigest()},
            'rows': {'12345A': {'name': 'Issuer', 'market': 'KOSDAQ', 'score': 25, 'rank': 1,
                                'available': 90, 'asof': day, 'oldest_asof': day}},
        }).encode()
        self.observation = capture(self.score, self.market, captured_at=self.capture_time,
                                   model_version='v3-saved-model', source_commit='a' * 40,
                                   run_id='42', run_attempt='1')
        self.committed_at = self.capture_time - timedelta(seconds=5)

    def test_rebuilds_frozen_fields_from_exact_sources(self):
        self.assertEqual(verify_observation(self.observation, self.score, self.market, self.committed_at),
                         {'listed_count': 1, 'ranked_count': 1, 'reference_count': 0})

    def test_rejects_drift_even_when_source_hashes_still_match(self):
        self.observation['rows']['12345A'][3] = True
        with self.assertRaisesRegex(ValueError, 'forward_score_observation_differs_from_saved_sources'):
            verify_observation(self.observation, self.score, self.market, self.committed_at)

    def test_rejects_stale_source_bytes_or_future_commit(self):
        for score, market, committed_at, error in (
            (self.score + b' ', self.market, self.committed_at, 'forward_score_source_sha256_mismatch'),
            (self.score, self.market + b' ', self.committed_at, 'forward_score_source_sha256_mismatch'),
            (self.score, self.market, self.capture_time + timedelta(seconds=1),
             'forward_score_source_commit_after_capture'),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    verify_observation(self.observation, score, market, committed_at)
