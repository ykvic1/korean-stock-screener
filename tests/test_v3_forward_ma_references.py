"""Regress price-only contact across official closing and entry archives."""
from datetime import date, timedelta
import json
import unittest

from v3_forward_ma_references import STATES, summarize


class ForwardMaReferenceTests(unittest.TestCase):
    def setUp(self):
        self.code = '005930'
        score_day = '2026-11-17'
        self.observation = {'score_asof': score_day,
                            'rows': {self.code: ['종목', 'KOSPI', 80, 1]}}
        self.openings = {self.code: ['official_open_observed', 102]}
        self.signal = {'date': score_day, 'source_sha256': {'KOSPI': 'a' * 64},
                       'rows': {self.code: ['KOSPI', '종목', 'traded', 100, 110, 99, 110, 500]}}
        self.entry = {'date': '2026-11-18', 'source_sha256': {'KOSPI': 'b' * 64},
                      'rows': {self.code: ['KOSPI', '종목', 'traded', 102, 111, 100, 105, 500]}}
        self.entry_basis = {'date': self.entry['date'], 'source_sha256': self.entry['source_sha256'],
                            'rows': {self.code: ['KOSPI', '종목', 105, 500, 110, True]}}
        end = date.fromisoformat(score_day)
        self.history = []
        for i in range(59, -1, -1):
            self.history.append((
                (end - timedelta(days=i)).isoformat(),
                {'source_sha256': self.signal['source_sha256'],
                 'rows': {self.code: ['KOSPI', '종목', 100 if i else 110, 500, 100, True]}},
            ))

    def summarize(self, *, notices=None):
        return summarize(self.observation, self.openings, self.signal, self.entry,
                         self.history, self.entry_basis, notices or {})

    def test_observed_intraday_contact_with_unrounded_averages_and_no_identifiers(self):
        result = self.summarize()
        self.assertEqual(result['status'], 'compared')
        self.assertEqual(result['archived_history_sessions'], 60)
        self.assertEqual(result['score_impact'], 0)
        self.assertFalse(result['performance_validated'])
        for group in result['groups'].values():
            self.assertEqual(group['matched_openings'], 1)
            for period in ('20', '60'):
                self.assertEqual(group[period]['intraday_average_touched'], 1)
                self.assertEqual(set(group[period]), set(STATES))
                self.assertEqual(sum(group[period].values()), 1)
        self.assertNotIn(self.code, json.dumps(result))

    def test_history_gap_and_share_count_change_leave_shorter_period_usable(self):
        day, panel = self.history[-35]
        self.history[-35] = (day, None)
        top = self.summarize()['groups']['top20']
        self.assertEqual(top['20']['intraday_average_touched'], 1)
        self.assertEqual(top['60']['history_session_missing'], 1)
        self.history[-35] = (day, panel)
        panel['rows'][self.code][3] = 250
        top = self.summarize()['groups']['top20']
        self.assertEqual(top['20']['intraday_average_touched'], 1)
        self.assertEqual(top['60']['share_count_changed'], 1)

    def test_entry_close_basis_and_reviewed_actions_block_contact(self):
        self.entry_basis['rows'][self.code][4] = 109
        top = self.summarize()['groups']['top20']
        self.assertEqual(top['20']['price_basis_discontinuous'], 1)
        top = self.summarize(notices={(self.entry['date'], self.code): {}})['groups']['top20']
        self.assertEqual(top['60']['reviewed_entry_action'], 1)
        self.entry_basis['source_sha256'] = {'KOSPI': 'c' * 64}
        result = self.summarize()
        self.assertEqual(result['status'], 'waiting_for_matching_entry_archive')
        self.assertNotIn('groups', result)
