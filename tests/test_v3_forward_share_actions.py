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
    HOLDER_NEUTRAL_ACTIONS, PRICE_ACTIONS, REGISTER, REQUIRES_TERMS, ShareReviewError,
    collect_share_reviews,
    reconcile, reviewed_actions,
)


def panel(day, shares=1200000, *, code='008500', market='KOSPI', name='일정실업'):
    return {'date': day, 'source_sha256': {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64},
            'rows': {code: [market, name, 'no_reported_volume', None, None, None, None, shares]}}


class ForwardShareActionTest(unittest.TestCase):
    def setUp(self):
        self.raw = REGISTER.read_bytes()
        self.digest = sha256(self.raw).hexdigest()
        self.notices = reviewed_actions(self.raw)

    def test_reviewed_counts_match_only_on_the_listing_session(self):
        self.assertTrue({notice['action'] for notice in self.notices.values()} >= set(PRICE_ACTIONS))
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

    def test_holder_neutral_terms_require_1_to_1_and_the_right_direction(self):
        for action in HOLDER_NEUTRAL_ACTIONS:
            before, after = (12, 10) if action == 'treasury_cancellation' else (10, 12)
            for old_count, new_count, new_units, expected in (
                (before, after, 1, True),
                (after, before, 1, False),
                (before, after, 2, False),
                (before, before, 1, False),
            ):
                with self.subTest(action=action, before=old_count, after=new_count, units=new_units):
                    header, example, *_ = self.raw.decode().splitlines()
                    row = example.split(',')
                    row[3:9] = ['2026-11-17', action, str(old_count), str(new_count), str(new_units), '1']
                    terms = ('https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261026900188'
                             if action in REQUIRES_TERMS else '')
                    raw = (header + ',terms_url\n' + ','.join(row + [terms]) + '\n').encode()
                    if expected:
                        self.assertEqual(reviewed_actions(raw)['2026-11-17', '008500']['action'], action)
                    else:
                        with self.assertRaisesRegex(ShareReviewError, 'invalid_reviewed_action_record'):
                            reviewed_actions(raw)

    def test_treasury_cancellation_requires_separate_official_terms(self):
        header, example, *_ = self.raw.decode().splitlines()
        row = example.split(',')
        row[3:9] = ['2026-11-17', 'treasury_cancellation', '12', '10', '1', '1']
        for terms in ('', row[9], 'https://example.org/cancellation'):
            with self.subTest(terms=terms):
                raw = (header + ',terms_url\n' + ','.join(row + [terms]) + '\n').encode()
                with self.assertRaisesRegex(ShareReviewError, 'invalid_reviewed_action_record'):
                    reviewed_actions(raw)

    def test_reviewed_rename_requires_same_market_and_exact_dated_names_and_shares(self):
        header, example, *_ = self.raw.decode().splitlines()
        row = example.split(',')
        row[2:9] = ['새 이름', '2026-11-17', 'rename', '1200000', '1200000', '1', '1']

        def parse(prior_name):
            raw = header + ',prior_name\n' + ','.join(row + [prior_name]) + '\n'
            return reviewed_actions(raw.encode())

        notices = parse('일정실업')
        for invalid in ('', '새이름'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ShareReviewError):
                    parse(invalid)

        prior = panel('2026-11-16')
        for after, reason in (
            (panel('2026-11-17', name='새이름'), 'notice_matched'),
            (panel('2026-11-18', name='새이름'), 'identity_changed'),
            (panel('2026-11-17', name='다른종목'), 'identity_changed'),
            (panel('2026-11-17', name='새이름', market='KOSDAQ'), 'identity_changed'),
            (panel('2026-11-17', shares=1200001, name='새이름'), 'notice_mismatch'),
        ):
            with self.subTest(after=after, reason=reason):
                _, cases = reconcile(prior, after, notices, self.digest)
                self.assertEqual(cases['008500']['state'], reason)

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

    def test_pipeline_versions_only_changed_session_inputs_and_reuses_saved_results(self):
        early, gap, late = '2025-12-12', '2025-12-15', '2025-12-16'
        panels = {early: panel(early), gap: panel(gap, 6000000), late: panel(late, 6000000)}
        storage = MemoryStore()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily, out, register = root / 'daily', root / 'review', root / 'notices.csv'
            daily.mkdir()
            register.write_bytes(self.raw)

            def publish(day):
                (daily / f'{day}.json').write_text(json.dumps({
                    'status': 'archived_privately', 'schema': DAILY_SCHEMA, 'date': day,
                    'source_sha256': panels[day]['source_sha256'],
                }))

            for day in panels:
                publish(day)
            with (patch('v3_forward_share_actions.OUT', out),
                  patch('v3_forward_share_actions.DAILY_STATUS', daily),
                  patch('v3_forward_share_actions.REGISTER', register),
                  patch('v3_forward_share_actions.read_daily', side_effect=lambda day, _: panels[day]) as reader,
                  patch('builtins.print')):
                now = timestamp('2025-12-16T11:45:00+00:00')
                collect_share_reviews(now, lambda: storage)
                original_blobs = dict(storage.blobs)
                original_status = {day: (out / f'{day}.json').read_bytes() for day in (gap, late)}
                self.assertEqual(len(original_blobs), 2)

                # Remove unrelated dates and change CSV line endings without reprocessing.
                rows = self.raw.decode().splitlines()
                session_rows = [line for line in rows[1:] if ',2025-12-15,' in line]
                register.write_bytes(('\r\n'.join([rows[0], *session_rows]) + '\r\n').encode())
                reader.reset_mock()
                collect_share_reviews(now, lambda: storage)
                reader.assert_not_called()

                # Removing the relevant notice changes only its session's evidence.
                register.write_text(rows[0] + '\n')
                collect_share_reviews(now, lambda: storage)
                self.assertEqual(len(storage.blobs), 3)
                self.assertEqual((out / f'{late}.json').read_bytes(), original_status[late])
                states = json.loads((out / f'{gap}.json').read_bytes())['states']
                self.assertEqual(states['change_needs_notice'], 1)

                # A source correction creates a new version for each adjacent pair.
                panels[gap]['source_sha256'] = {'KOSPI': 'c' * 64, 'KOSDAQ': 'b' * 64}
                publish(gap)
                collect_share_reviews(now, lambda: storage)
                self.assertEqual(len(storage.blobs), 5)
                for path, blob in original_blobs.items():
                    self.assertEqual(storage.blobs[path], blob)
                reader.reset_mock()
                collect_share_reviews(now, lambda: storage)
                reader.assert_not_called()

                # Lost public status after a private write recovers without overwriting.
                (out / f'{gap}.json').unlink()
                collect_share_reviews(now, lambda: storage)
                self.assertEqual(len(storage.blobs), 5)
                self.assertTrue((out / f'{gap}.json').exists())
