"""Actual server/browser formula parity and dated per-issuer export checks."""
import copy
import csv
from io import StringIO
import json
from pathlib import Path
import subprocess
import unittest

from v3_personal_score_adjustments import build, profile, to_csv, SCHEMA
from v3_issuer_factor_ledger import SCHEMA as LEDGER_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


def sources():
    day = '2026-11-03'
    members = [{'code': f'{n:06d}', 'name': f'Synthetic {n}', 'market': 'KOSPI'} for n in (1, 2, 3)]
    stocks = [{**m, 'price_date': day, 'price': 100, 'stop_line': 94, 'stop_pct': -6,
               'market_cap_eok': 5000 if m['code'] == '000001' else 1000,
               'price60': 10, 'risk_flags': []} for m in members[:2]]
    ledger = {'schema': LEDGER_SCHEMA, 'market_asof': day, 'listed_total': 3,
              'investment_score_certified': False, 'mixed_date_score_research_only': True,
              'missing_is_not_zero': True, 'rows': {}}
    for m in members:
        ledger['rows'][m['code']] = {**m, 'active': True, 'factors': {
            'technical': {'points': 20, 'available': 25, 'asof': '2026-11-02',
                          'source': 'current_close_replay'}}}
    return {'date': day, 'stocks': stocks}, {'date': day, 'listed_total': 3, 'rows': members}, ledger


class PersonalAdjustmentTests(unittest.TestCase):
    def test_saved_whole_market_retained_scores_and_ranks_match_browser(self):
        def read(name):
            return json.loads((ROOT / 'docs' / name).read_text())
        market, catalog, ledger = (read('data.json'), read('v22-market-catalog.json'),
                                   read('v3-issuer-last-observed-factors.json'))
        actual, summary = build(market, catalog, ledger)
        raw = {r['code']: r for r in market['stocks']}
        keys = ('code', 'name', 'market', 'price_date', 'market_cap_eok',
                'price', 'stop_line', 'stop_pct', 'price60', 'risk_flags')
        browser = []
        for item in actual:
            if item['price_observed']:
                stock = {k: raw[item['code']].get(k) for k in keys}
                stock['v3_last_known_factors'] = ledger['rows'][item['code']]['factors']
                browser.append({'r': stock})
        program = """
const fs=require('node:fs'),w=require('./docs/workspace.js');
const rows=JSON.parse(fs.readFileSync(0,'utf8')).sort(w.compareStableResearchScores);
console.log(JSON.stringify(rows.filter(x=>w.researchComposite(x.r).earned!==null).map((x,i)=>{
 const c=w.researchComposite(x.r),p=w.stabilityProfile(x.r,c);
 return {code:x.r.code,rank:i+1,base:c.earned,available:c.available,score:p.score};
})));
"""
        result = subprocess.run(['node', '-e', program], input=json.dumps(browser),
                                capture_output=True, text=True, cwd=ROOT, check=True)
        js = json.loads(result.stdout)
        by = {row['code']: row for row in actual}
        self.assertEqual(len(js), summary['ranked_issuers'])
        for row in js:
            expected = by[row['code']]
            self.assertEqual((row['rank'], row['base'], row['available'], row['score']),
                             (expected['personal_rank'], expected['base_score'],
                              expected['available'], expected['adjusted_score']), row['code'])

    def test_server_matches_live_browser_at_all_piecewise_boundaries(self):
        examples = []
        for cap in (None, 0, True, 999, 1000, 1001, 2500, 4999, 5000, 50000):
            for distance in (.5, 2.99, 3, 4.95, 6, 9, 11.99, 12, 15, 20, 25, 70):
                examples.append({'market_cap_eok': cap, 'price': 100,
                                 'stop_line': 100-distance, 'stop_pct': -distance,
                                 'price_date': '2026-11-03', 'price60': 10, 'risk_flags': []})
        examples += [dict(examples[50], price60=40), dict(examples[50], risk_flags=['추격 주의']),
                     dict(examples[50], stop_pct=-90), {}, {'market_cap_eok': 2500}]
        program = """
const fs=require('node:fs'),w=require('./docs/workspace.js');
const rows=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(rows.map(r=>w.stabilityProfile(r,{earned:63.1}))));
"""
        result = subprocess.run(['node', '-e', program], input=json.dumps(examples),
                                capture_output=True, text=True, cwd=ROOT, check=True)
        expected = json.loads(result.stdout)
        for row, js in zip(examples, expected):
            actual = profile(row, 63.1)
            for py, key in (('capital_deduction', 'capitalDeduction'),
                            ('stop_adjustment', 'stopAdjustment'), ('deduction', 'deduction'),
                            ('adjusted_score', 'score'), ('missing_adjustment_sources', 'missing'),
                            ('stop_distance_pct', 'stopDistancePct')):
                self.assertEqual(actual[py], js[key], (row, key))

    def test_actual_one_day_amount_never_affects_score_and_missing_total_stays_missing(self):
        base = {'market_cap_eok': 1000, 'price': 100, 'stop_line': 94, 'stop_pct': -6}
        self.assertEqual(profile({**base, 'trading_eok': 0}, 60),
                         profile({**base, 'trading_eok': 1e12}, 60))
        self.assertEqual(profile(base, 60)['adjusted_score'], 58)
        self.assertIsNone(profile(base, None)['adjusted_score'])

    def test_export_retains_dated_factors_and_unpriced_history_without_inventing_rank(self):
        inputs = sources()
        before = copy.deepcopy(inputs)
        rows, summary = build(*inputs)
        by = {row['code']: row for row in rows}
        self.assertEqual(by['000001']['personal_rank'], 1)
        self.assertEqual(by['000002']['personal_rank'], 2)
        self.assertIsNone(by['000003']['personal_rank'])
        self.assertIsNone(by['000003']['price_asof'])
        self.assertEqual(by['000003']['base_score'], 20)
        self.assertEqual(by['000001']['oldest_factor_asof'], '2026-11-02')
        self.assertEqual(by['000001']['retained_factor_count'], 1)
        self.assertEqual(summary['schema'], SCHEMA)
        self.assertEqual(summary['ranked_issuers'], 2)
        self.assertFalse(summary['official_investment_scores_certified'])
        exported = list(csv.DictReader(StringIO(to_csv(rows).lstrip('\ufeff'))))
        self.assertEqual(exported[0]['안정형순위'], '1')
        self.assertEqual(exported[2]['가격기준일'], '')
        self.assertEqual(exported[2]['technical_기준일'], '2026-11-02')
        self.assertEqual(inputs, before)

    def test_renamed_issuer_future_factor_and_wrong_census_cannot_enter_rank(self):
        for change in ('identity', 'future', 'census'):
            market, catalog, ledger = sources()
            if change == 'identity':
                ledger['rows']['000001']['name'] = 'Prior issuer'
            elif change == 'future':
                ledger['rows']['000001']['factors']['technical']['asof'] = '2026-11-04'
            else:
                catalog['listed_total'] = 4
            with self.subTest(change=change), self.assertRaises(ValueError):
                build(market, catalog, ledger)


if __name__ == '__main__':
    unittest.main()
