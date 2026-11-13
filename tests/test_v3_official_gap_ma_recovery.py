"""Official-gap recovery cannot drop a market day or change a valid quote."""
from datetime import datetime
import unittest

import pandas as pd

from v3_official_gap_ma_recovery import FIELDS, GAP, KST, NEIGHBORS, repair, source_frame

DATES = [NEIGHBORS[0], GAP, NEIGHBORS[1]]
TICKER = '000001.KS'


def payload():
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol': TICKER, 'currency': 'KRW', 'exchangeTimezoneName': 'Asia/Seoul',
                 'instrumentType': 'EQUITY'},
        'timestamp': [int(datetime.fromisoformat(d).replace(tzinfo=KST).timestamp()) for d in DATES],
        'indicators': {'quote': [{f.lower(): [100., None, 120.] for f in FIELDS}]},
    }]}}


class OfficialGapMARecoveryTests(unittest.TestCase):
    def test_official_row_only_replaces_all_null_gap(self):
        original = source_frame(payload(), TICKER, DATES,
                                {NEIGHBORS[0]: 100., NEIGHBORS[1]: 120.})
        verified = {NEIGHBORS[0]: (100.,)*5, GAP: (105., 115., 103., 110., 30.),
                    NEIGHBORS[1]: (120.,)*5}
        filled = repair(original, verified)
        self.assertEqual(filled.loc[GAP].tolist(), list(verified[GAP]))
        pd.testing.assert_frame_equal(filled.drop(index=GAP), original.drop(index=GAP))
        self.assertTrue(original.loc[GAP].isna().all())
        self.assertIsNone(repair(filled, verified))
        self.assertIsNone(repair(original.drop(index=GAP), verified))
        self.assertIsNone(repair(original, dict(verified, **{NEIGHBORS[0]: (90.,)*5})))

    def test_calendar_gaps_and_mismatching_anchors_are_rejected(self):
        anchors = {NEIGHBORS[0]: 100., NEIGHBORS[1]: 120.}
        self.assertIsNone(source_frame(payload(), TICKER, list(NEIGHBORS), anchors))
        self.assertIsNone(source_frame(payload(), TICKER, DATES, dict(anchors, **{NEIGHBORS[0]: 90.})))
        self.assertIsNone(source_frame(payload(), '000002.KS', DATES, anchors))


if __name__ == '__main__':
    unittest.main()
