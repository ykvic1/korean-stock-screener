from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import v3_score_observation_capture as observation


def encode(obj):
    return json.dumps(obj).encode()


class ScoreObservationTests(unittest.TestCase):
    def setUp(self):
        self.market = {
            'date': '2025-11-13',
            'price_feed': 'unverified_daily',
            'price_regular_session_verified': False,
            'stocks': [{
                'code': '005930', 'name': 'One', 'market': 'KOSPI',
                'price_date': '2025-11-13', 'price': 100,
                'v3_structural_stop_reference': {
                    'asof': '2025-11-13', 'reference_line': 80,
                    'unscored_reference_only': True, 'score_impact': 0,
                },
            }],
        }
        self.score = {
            'version': observation.VERSION, 'asof': '2025-11-13',
            'investment_score_certified': False, 'performance_validated': False,
            'listed_total': 2,
            'source_hashes': {'data.json': sha256(encode(self.market)).hexdigest()},
            'rows': {
                '005930': {'name': 'One', 'market': 'KOSPI', 'score': 25, 'rank': 1,
                           'available': 90, 'asof': '2025-11-13', 'oldest_asof': '2025-11-12'},
                '12345A': {'name': 'Two', 'market': 'KOSDAQ', 'score': None, 'rank': None,
                           'available': 0, 'asof': '2025-11-13', 'oldest_asof': None},
            },
        }
        self.when = datetime(2025, 11, 13, 7, 30, tzinfo=timezone.utc)

    def capture(self, when=None):
        return observation.capture(
            encode(self.score), encode(self.market), captured_at=when or self.when,
            source_commit='a'*40, run_id='42', run_attempt='1',
        )

    def test_exam_close_and_late_capture_keep_actual_timestamp(self):
        early = datetime(2025, 11, 13, 7, 29, 59, tzinfo=timezone.utc)
        self.assertIsNone(self.capture(early))
        self.assertIsNotNone(self.capture())
        late = datetime(2025, 11, 16, 10, tzinfo=timezone.utc)
        result = self.capture(late)
        self.assertEqual(result['captured_at_utc'], '2025-11-16T10:00:00.000000+00:00')
        self.assertEqual(result['score_asof'], '2025-11-13')
        self.assertEqual(result['source_score_sha256'], sha256(encode(self.score)).hexdigest())
        self.assertEqual(result['rows']['005930'][2:], [25, 1, 90, '2025-11-13', '2025-11-12', 80])
        self.assertEqual(result['rows']['12345A'][2:], [None, None, 0, '2025-11-13', None, None])
        self.assertFalse(result['performance_validated'])

    def test_hash_drift_defers_and_stale_reference_stays_empty(self):
        self.market['stocks'][0]['v3_structural_stop_reference']['asof'] = '2025-11-12'
        self.assertIsNone(self.capture())
        self.score['source_hashes']['data.json'] = sha256(encode(self.market)).hexdigest()
        result = self.capture()
        self.assertEqual(result['reference_count'], 0)
        self.assertIsNone(result['rows']['005930'][-1])

    def test_same_date_capture_is_never_replaced(self):
        with TemporaryDirectory() as folder, patch.object(observation, 'ARCHIVE', Path(folder)):
            with patch('sys.argv', ['capture', '--run-id', '42', '--run-attempt', '1']), patch(
                'subprocess.check_output', side_effect=['a'*40, encode(self.score), encode(self.market)]*2,
            ):
                observation.main()
                path = Path(folder) / '2025-11-13.json'
                first = path.read_bytes()
                observation.main()
                self.assertEqual(path.read_bytes(), first)
