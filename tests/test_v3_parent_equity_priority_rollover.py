"""A dated priority lag is a no-call wait, never a failed or empty collection."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import unittest

import v3_dart_parent_equity_recovery as source
from v3_dart_rolling_book_reference import SCHEMA as BOOK_SCHEMA


class ParentEquityPriorityRolloverTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent.parent / 'docs'
        self.report = json.loads((root / 'v3-operational-research-scores.json').read_text())
        self.comparison = json.loads((root / 'v3-operational-research-comparison.json').read_text())
        day = self.report['asof']
        # Frozen score identity, independent of the mutable live price census.
        self.catalog = {'date': day, 'listed_total': self.report['listed_total'],
                        'rows': [{'code': c, 'name': r['name'], 'market': r['market']}
                                 for c, r in self.report['rows'].items()]}
        self.book = {'asof': day, 'schema': BOOK_SCHEMA,
                     'historical_point_in_time_verified': False,
                     'official_exchange_pbr_verified': False}
        self.cash = {'asof': day, 'schema': source.cf.SCHEMA,
                     'historical_PIT_certified': False, 'raw_dart_rows_published': False}

    def ready(self):
        return source.priority_ready(self.report, self.comparison, self.catalog,
                                     self.book, self.cash)

    def test_aligned_priority_ready_and_new_close_waits_without_mutation(self):
        self.assertTrue(self.ready())
        self.catalog['date'] = (date.fromisoformat(self.report['asof']) + timedelta(days=3)).isoformat()
        before = deepcopy((self.report, self.comparison, self.catalog, self.book, self.cash))
        self.assertFalse(self.ready())
        self.assertEqual((self.report, self.comparison, self.catalog, self.book, self.cash), before)
        self.book['asof'] = self.catalog['date']
        self.assertFalse(self.ready(), 'cashflow and score must also catch up')

    def test_future_expired_and_noncanonical_dates_remain_errors(self):
        day = date.fromisoformat(self.catalog['date'])
        for invalid in ((day + timedelta(days=1)).isoformat(),
                        (day - timedelta(days=11)).isoformat(), '2026-11-3'):
            with self.subTest(invalid=invalid):
                self.book['asof'] = invalid
                with self.assertRaises(source.ParentEquityRecoveryError):
                    self.ready()

    def test_forged_reports_and_source_certification_never_become_a_wait(self):
        self.catalog['date'] = (date.fromisoformat(self.report['asof']) + timedelta(days=3)).isoformat()
        for obj, key, bad in ((self.report, 'investment_score_certified', True),
                              (self.book, 'schema', 'untrusted'),
                              (self.cash, 'raw_dart_rows_published', True)):
            old = obj[key]
            with self.subTest(key=key), self.assertRaises(source.ParentEquityRecoveryError):
                obj[key] = bad
                self.ready()
            obj[key] = old

    def test_workflow_skips_live_recovery_when_priority_is_not_ready(self):
        text = (Path(__file__).resolve().parent.parent /
                '.github/workflows/v3-parent-equity-recovery.yml').read_text()
        self.assertIn("needs.offline.outputs.ready == 'true'", text)
        self.assertIn('ready: ${{ steps.priority.outputs.ready }}', text)
