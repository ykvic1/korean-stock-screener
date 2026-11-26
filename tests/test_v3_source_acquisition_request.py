"""Explicit new-session collection must not pretend old sources are current."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from v3_aftermarket_cutoff import KST
from v3_source_acquisition_request import prepare, execute, SourceRequestError


class SourceAcquisitionRequestTest(unittest.TestCase):
    def setUp(self):
        self.catalog = {'date': '2026-11-13', 'rows': []}
        self.request = {'id': '20261117-catchup', 'asof': '2026-11-16',
                        'workflow': 'full-market-batches.yml', 'mode': 'publish'}
        self.now = datetime(2026, 11, 17, 9, tzinfo=KST)

    def test_friday_to_monday_catchup_keeps_saved_catalog_unchanged(self):
        original = deepcopy(self.catalog)
        command, report = prepare(self.request, self.catalog, 'owner/repo', now=self.now)
        self.assertIn('asof=2026-11-16', command)
        self.assertTrue(report['new_session_requested'])
        self.assertTrue(report['date_pinned_roster_verification_required'])
        self.assertFalse(report['collection_completed'])
        self.assertEqual(report['points_added_by_dispatch'], 0)
        self.assertEqual(self.catalog, original)

    def test_same_day_replay_remains_supported(self):
        request = dict(self.request, asof='2026-11-13',
                       workflow='v3-operational-research-score.yml', mode='replay')
        command, report = prepare(request, self.catalog, 'owner/repo', now=self.now)
        self.assertNotIn('asof=2026-11-13', command)
        self.assertFalse(report['new_session_requested'])

    def test_current_intraday_future_weekend_and_older_dates_are_blocked(self):
        for day in ('2026-11-17', '2026-11-18', '2026-11-14', '2026-11-12'):
            with self.subTest(day=day):
                with self.assertRaises(SourceRequestError):
                    prepare(dict(self.request, asof=day), self.catalog, 'owner/repo', now=self.now)
        with self.assertRaises(SourceRequestError):
            prepare(self.request, {'date': '2026-11-01'}, 'owner/repo', now=self.now)

    def test_fiscal_replay_cannot_advance_the_market_date(self):
        with self.assertRaises(SourceRequestError):
            prepare(dict(self.request, workflow='v3-operational-research-score.yml', mode='replay'),
                    self.catalog, 'owner/repo', now=self.now)

    def test_only_missing_downstream_jobs_can_be_requested_at_saved_census_day(self):
        for workflow, mode in (
            ('v3-rolling-dart-annual-book.yml', 'collect'),
            ('v3-dart-bulk-four-year.yml', 'collect'),
            ('v3-xbrl-eps-recovery.yml', 'recover'),
            ('v3-krx-money-week-repair.yml', 'recover'),
            ('v3-saved-close-path-recovery.yml', 'recover'),
            ('v3-dart-revenue-2022.yml', 'collect'),
            ('v3-dart-owner-profit-probe.yml', 'collect'),
            ('v3-regular-close-indicators.yml', 'derive'),
        ):
            with self.subTest(workflow=workflow):
                request = dict(self.request, asof=self.catalog['date'],
                               workflow=workflow, mode=mode)
                command, report = prepare(request, self.catalog, 'owner/repo', now=self.now)
                self.assertEqual(command[3], workflow)
                self.assertFalse(report['new_session_requested'])
                self.assertFalse(report['collection_completed'])
                self.assertEqual(report['points_added_by_dispatch'], 0)
                if workflow == 'v3-dart-bulk-four-year.yml':
                    self.assertEqual(command[-4:], ['-f', 'mode=live', '-f', 'groups=3'])
                elif workflow in ('v3-dart-revenue-2022.yml', 'v3-dart-owner-profit-probe.yml'):
                    self.assertEqual(command[-2:], ['-f', 'mode=live'])
                else:
                    self.assertNotIn('-f', command)
                for invalid in (dict(request, asof='2026-11-16'),
                                dict(request, restore_schedule=True),
                                dict(request, mode='publish')):
                    run = Mock()
                    with self.assertRaises(SourceRequestError):
                        execute(invalid, self.catalog, 'owner/repo', now=self.now, run=run)
                    run.assert_not_called()

    def test_invalid_operations_dates_and_clock_are_rejected(self):
        for change in ({'workflow': 'unlisted.yml'}, {'mode': 'audit'},
                       {'asof': '2026-02-30'}, {'asof': '2026-11-6'},
                       {'restore_schedule': 'true'}, {'extra': 'field'}):
            with self.subTest(change=change):
                with self.assertRaises(SourceRequestError):
                    prepare(dict(self.request, **change), self.catalog, 'owner/repo', now=self.now)
        with self.assertRaises(SourceRequestError):
            prepare(self.request, self.catalog, 'owner/repo', now=datetime(2026, 11, 17))

    def test_explicit_schedule_restore_only_enables_a_known_disabled_workflow(self):
        run = Mock(return_value=SimpleNamespace(stdout=json.dumps({
            'path': '.github/workflows/full-market-batches.yml', 'state': 'disabled_manually'})))
        report = execute(dict(self.request, restore_schedule=True), self.catalog,
                         'owner/repo', now=self.now, run=run)
        self.assertTrue(report['schedule_restored'])
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[1].args[0][:3], ['gh', 'workflow', 'enable'])
        self.assertIn('asof=2026-11-16', run.call_args.args[0])

    def test_active_or_unrequested_schedule_is_not_enabled_again(self):
        run = Mock(return_value=SimpleNamespace(stdout=json.dumps({
            'path': '.github/workflows/full-market-batches.yml', 'state': 'active'})))
        report = execute(dict(self.request, restore_schedule=True), self.catalog,
                         'owner/repo', now=self.now, run=run)
        self.assertFalse(report['schedule_restored'])
        self.assertEqual(run.call_count, 2)
        run.reset_mock()
        execute(self.request, self.catalog, 'owner/repo', now=self.now, run=run)
        self.assertEqual(run.call_count, 1)

    def test_unknown_workflow_state_never_authorizes_a_dispatch(self):
        for state in ('deleted', 'unexpected'):
            run = Mock(return_value=SimpleNamespace(stdout=json.dumps({
                'path': '.github/workflows/full-market-batches.yml', 'state': state})))
            with self.assertRaises(SourceRequestError):
                execute(dict(self.request, restore_schedule=True), self.catalog,
                        'owner/repo', now=self.now, run=run)
            self.assertEqual(run.call_count, 1)
