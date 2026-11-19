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

    def summarize(self, *, notices=None, intervening=()):
        return summarize(self.observation, self.openings, self.signal, self.entry,
                         self.history, self.entry_basis, notices or {}, intervening=intervening)

    def issuance(self, before, after):
        return {'name': '종목', 'market': 'KOSPI', 'action': 'bond_conversion',
                'shares_before': before, 'shares_after': after, 'new_units': 1, 'old_units': 1}

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
        top = self.summarize(notices={
            (self.entry['date'], self.code): {'action': 'split'}})['groups']['top20']
        self.assertEqual(top['60']['reviewed_entry_action'], 1)
        self.entry_basis['source_sha256'] = {'KOSPI': 'c' * 64}
        result = self.summarize()
        self.assertEqual(result['status'], 'waiting_for_matching_entry_archive')
        self.assertNotIn('groups', result)

    def test_historical_review_restores_only_periods_that_cross_exact_neutral_action(self):
        for _, panel in self.history[:-34]:
            panel['rows'][self.code][3] = 450
        event_day, event_panel = self.history[-34]
        key = event_day, self.code
        baseline = self.summarize()
        self.assertEqual(baseline['groups']['top20']['60']['share_count_changed'], 1)
        amended = self.summarize(notices={key: self.issuance(450, 500)})
        top = amended['groups']['top20']
        self.assertNotEqual(baseline['reviewed_ma_notices_sha256'], amended['reviewed_ma_notices_sha256'])
        self.assertEqual(top['using_reviewed_share_continuity'], {'20': 0, '60': 1})
        self.assertEqual(top['60']['intraday_average_touched'], 1)
        top = self.summarize(notices={key: self.issuance(451, 500)})['groups']['top20']
        self.assertEqual(top['60']['reviewed_share_basis_unresolved'], 1)
        event_panel['rows'][self.code][4] = 101
        top = self.summarize(notices={key: self.issuance(450, 500)})['groups']['top20']
        self.assertEqual(top['60']['price_basis_discontinuous'], 1)
        self.assertEqual(top['using_reviewed_share_continuity']['60'], 0)

    def test_entry_day_issuance_also_requires_matching_before_and_after_counts(self):
        self.entry['rows'][self.code][-1] = 550
        self.entry_basis['rows'][self.code][3] = 550
        key = self.entry['date'], self.code
        top = self.summarize(notices={key: self.issuance(500, 550)})['groups']['top20']
        self.assertEqual(top['using_reviewed_share_continuity'], {'20': 1, '60': 1})
        self.assertEqual(top['20']['intraday_average_touched'], 1)
        top = self.summarize(notices={key: self.issuance(500, 551)})['groups']['top20']
        self.assertEqual(top['60']['reviewed_entry_action'], 1)

    def test_intervening_archive_and_any_intermediate_price_or_share_break_are_required(self):
        self.signal['date'] = self.observation['score_asof'] = '2026-11-16'
        self.history = [
            ((date.fromisoformat(day) - timedelta(days=1)).isoformat(), panel)
            for day, panel in self.history]
        day = '2026-11-17'
        missing = self.summarize(intervening=[(day, None)])
        self.assertEqual(missing['status'], 'waiting_for_intervening_archive')
        self.assertNotIn('groups', missing)
        middle = ['KOSPI', '종목', 150, 500, 110, True]
        bridge = [(day, {'date': day, 'source_sha256': {'KOSPI': 'c' * 64},
                         'rows': {self.code: middle}})]
        entry = self.entry_basis['rows'][self.code]
        entry[4] = 150
        # The intervening close never enters the average frozen at the signal.
        self.entry['rows'][self.code][5] = 101
        ok = self.summarize(intervening=bridge)
        self.assertEqual(ok['groups']['top20']['20']['average_not_touched'], 1)
        self.assertEqual(ok['intervening_source_sha256'][day], {'KOSPI': 'c' * 64})
        middle[4] = 109
        blocked = self.summarize(intervening=bridge)['groups']['top20']
        self.assertEqual(blocked['20']['pre_entry_price_basis_discontinuous'], 1)
        middle[4] = 110
        middle[3] = 550
        blocked = self.summarize(intervening=bridge)['groups']['top20']
        self.assertEqual(blocked['20']['pre_entry_share_basis_unresolved'], 1)
        self.entry['rows'][self.code][-1] = entry[3] = 550
        reviewed = self.summarize(intervening=bridge,
            notices={(day, self.code): self.issuance(500, 550)})['groups']['top20']
        self.assertEqual(reviewed['using_reviewed_share_continuity'], {'20': 1, '60': 1})
