"""Regress dated notice matching and prevent implicit shareholder adjustment."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from tests.test_v3_forward_entry import MemoryStore
from v3_forward_daily_prices import SCHEMA as DAILY_SCHEMA
from v3_forward_entry import timestamp
from v3_forward_share_actions import (
    REGISTER, ShareReviewError, collect_share_reviews, reconcile, reviewed_actions,
)


def panel(day, shares=1200000, *, code='008500', market='KOSPI', name='일정실업'):
    return {'date': day, 'source_sha256': {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64},
            'rows': {code: [market, name, 'no_reported_volume', None, None, None, None, shares]}}


class ForwardShareActionTest(unittest.TestCase):
    def setUp(self):
        self.raw = REGISTER.read_bytes()
        self.digest = sha256(self.raw).hexdigest()
        self.notices = reviewed_actions(self.raw)

    def test_four_reviewed_counts_match_only_on_the_listing_session(self):
        self.assertEqual(len(self.notices), 4)
        for (day, code), notice in self.notices.items():
            with self.subTest(code=code):
                args = {'code': code, 'market': notice['market'], 'name': notice['name']}
                prior = panel('2025-11-14', notice['shares_before'], **args)
                after = panel(day, notice['shares_after'], **args)
                summary, cases = reconcile(prior, after, self.notices, self.digest)
                self.assertEqual(summary['states']['notice_matched'], 1)
                self.assertEqual(cases[code]['new_units'], notice['new_units'])
                self.assertNotIn('ohlc_krw_per_previous_share', cases[code])
                self.assertNotIn('cases', summary)
                after['date'] = '2026-06-01'
                self.assertEqual(reconcile(prior, after, self.notices, self.digest)[1][code],
                                 {'state': 'change_needs_notice'})

    def test_no_ratio_for_wrong_counts_identity_or_missing_market_row(self):
        before = panel('2025-12-12')
        for after, reason in (
            (panel('2025-12-15', 5900000), 'notice_mismatch'),
            (panel('2025-12-15', 1200000), 'notice_mismatch'),
            (panel('2025-12-15', 6000000, name='다른종목'), 'identity_changed'),
            (panel('2025-12-15', None), 'share_count_unavailable'),
            (panel('2025-12-15', 6000000, code='000001'), 'absent'),
        ):
            with self.subTest(reason=reason):
                _, cases = reconcile(before, after, self.notices, self.digest)
                self.assertEqual(cases['008500'], {'state': reason})

    def test_trade_bar_conversion_uses_notice_ratio_and_exact_values(self):
        code = '121850'
        before = panel('2025-12-09', 30614175, code=code, market='KOSDAQ', name='코이즈')
        after = panel('2025-12-10', 3061417, code=code, market='KOSDAQ', name='코이즈')
        after['rows'][code][2:7] = ['traded', 7000, 7000, 5690, 5775]
        _, cases = reconcile(before, after, self.notices, self.digest)
        self.assertEqual(cases[code]['ohlc_krw_per_previous_share'],
                         ['700', '700', '569', '1155/2'])

    def test_register_rejects_inconsistent_verified_ratio(self):
        raw = self.raw.replace(b',6000000,5,1,', b',6000000,4,1,')
        with self.assertRaisesRegex(ShareReviewError, 'invalid_reviewed_action_record'):
            reviewed_actions(raw)

    def test_pipeline_does_not_bridge_an_uncollected_session(self):
        now = timestamp('2025-12-16T11:45:00+00:00')
        storage = MemoryStore()
        early, gap, late = '2025-12-12', '2025-12-15', '2025-12-16'
        panels = {early: panel(early), gap: panel(gap, 6000000), late: panel(late, 6000000)}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / 'daily'
            daily.mkdir()
            out = root / 'review'

            def publish(day):
                (daily / f'{day}.json').write_text(json.dumps({
                    'status': 'archived_privately', 'schema': DAILY_SCHEMA, 'date': day,
                    'source_sha256': panels[day]['source_sha256'],
                }))

            publish(early)
            publish(late)
            with (patch('v3_forward_share_actions.OUT', out),
                  patch('v3_forward_share_actions.DAILY_STATUS', daily),
                  patch('v3_forward_share_actions.read_daily', side_effect=lambda day, _: panels[day]) as reader,
                  patch('builtins.print')):
                collect_share_reviews(now, lambda: storage)
                self.assertFalse(out.exists())
                publish(gap)
                collect_share_reviews(now, lambda: storage)
                self.assertEqual(reader.call_count, 3)
                states = json.loads((out / f'{gap}.json').read_bytes())['states']
                self.assertEqual(states['notice_matched'], 1)
                self.assertEqual(len(storage.blobs), 2)
                collect_share_reviews(now, lambda: storage)
                self.assertEqual(reader.call_count, 3)
