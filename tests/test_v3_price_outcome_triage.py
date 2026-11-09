"""No-provider distinction between zero-volume references and real gaps."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from v22_active_market_catalog import build_active_catalog
from v3_price_outcome_triage import REASONS, build, main


def catalog():
    listing = {}
    stocks = []
    errors = []
    excludes = []
    for index in range(8):
        code = f'{100000 + index:06d}'
        listing[code] = {
            'code': code, 'name': f'시험{index}',
            'market': 'KOSPI' if index % 2 else 'KOSDAQ',
            'market_cap_eok': 1500, 'trading_eok': 0,
            'snapshot_volume': 0 if index in (1, 2) else
                               100 if index in (0, 3, 4) else None,
            'pbr': None, 'sector': None, 'valuation_source': None,
        }
        if index == 0:
            stocks.append({'code': code, 'name': f'시험{index}',
                           'market': 'KOSDAQ', 'price': 100,
                           'price_date': '2026-11-06', 'flows_confirmed': False,
                           'pbr': None})
        elif index in (1, 2, 3, 5):
            errors.append([code, '가격이력', 'SourceError'])
        elif index == 4:
            excludes.append([code, 'insufficient_valid_price_history'])
    snap = {'version': '2.0', 'status': 'success', 'date': '2026-11-06',
            'eligible_count': 8, 'examined_count': 6,
            'history_deferred_count': 2, 'history_scan_complete': False,
            'candidate_count': 1, 'history_usable_count': 1,
            'stocks': stocks, 'collection_errors': errors,
            'history_exclusions': excludes, 'error_count': len(errors),
            'flow_confirmed_count': 0, 'fundamental_count': 0}
    return build_active_catalog(snap, listing)


class PriceOutcomeTriageTests(unittest.TestCase):
    def test_unresolved_reclassification_never_promotes_points(self):
        output = build(catalog())
        counts = output['counts']
        self.assertEqual(output['listed_total'], 8)
        self.assertEqual(output['unresolved_count'], 7)
        self.assertEqual(counts[REASONS[0]], 1)
        self.assertEqual(counts[REASONS[1]], 2)
        self.assertEqual(counts[REASONS[2]], 1)
        self.assertEqual(counts[REASONS[3]], 1)
        self.assertEqual(counts[REASONS[4]], 1)
        self.assertEqual(counts[REASONS[5]], 2)
        self.assertEqual(sum(counts.values()), 8)
        self.assertFalse(output['krx_regular_only_volume_verified'])
        self.assertFalse(output['suspension_or_corporate_action_cause_verified'])
        self.assertFalse(output['investment_score_certified'])
        self.assertEqual(output['provider_calls'], 0)
        self.assertEqual(output['score_points_added'], 0)
        self.assertNotIn('source_volume', json.dumps(output))

    def test_zero_volume_reference_is_never_called_confirmed_halt(self):
        output = build(catalog())
        affected = [r for r in output['unresolved'] if
                    r['triage_reason'] == REASONS[1]]
        self.assertEqual(len(affected), 2)
        self.assertTrue(all(r['listing_zero_volume_reference'] is True
                            for r in affected))
        self.assertTrue(output['zero_trades_do_not_prove_exchange_suspension'])
        self.assertTrue(output['missing_listing_start_does_not_prove_prelisting'])
        self.assertTrue(output['no_missing_close_carried_forward'])

    def test_untrusted_census_fails_closed(self):
        good = catalog()
        for mutate in (
            lambda c: c['rows'].append(copy.deepcopy(c['rows'][0])),
            lambda c: c['rows'][1].__setitem__('code', c['rows'][0]['code']),
            lambda c: c['rows'][1].__setitem__('listing_zero_volume_reference', 0),
            lambda c: c['status_counts'].__setitem__('priced', 2),
        ):
            invalid = copy.deepcopy(good)
            mutate(invalid)
            with self.assertRaises(ValueError):
                build(invalid)

    def test_atomic_write_verify_and_stale_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            docs = Path(folder)
            (docs / 'v22-market-catalog.json').write_text(
                json.dumps(catalog(), ensure_ascii=False), encoding='utf-8')
            main(['--mode', 'write', '--docs', str(docs)])
            main(['--mode', 'verify', '--docs', str(docs)])
            stale = docs / 'v3-price-outcome-triage.json'
            stale.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'not_current'):
                main(['--mode', 'verify', '--docs', str(docs)])


if __name__ == '__main__':
    unittest.main()
