import copy
import json
from pathlib import Path
import subprocess
import unittest

from test_v3_dart_reported_eps import inputs, payload, BASE, DAY
from v3_dart_reported_eps import parse, snapshot
from v3_eps_valuation import evaluate, build, valid, MODEL, WEIGHTS
from v3_personal_score_adjustments import build as personal_build

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    catalog, book = inputs()
    evidence = parse(payload(), corp='00123456', base=BASE, on_day=DAY)
    source = snapshot(DAY, catalog, book, {BASE['code']: BASE}, {BASE['code']: evidence})
    components = {key: {'points': value, 'available': 5, 'asof': DAY,
                       'source': 'dart_fcf_subfactor' if key == 'fcf_yield' else 'dart_fiscal_subfactor'}
                  for key, value in {'pbr': 2, 'earnings_yield_pct': 3,
                                     'fcf_yield': 4, 'sales_to_market_cap': 1}.items()}
    ledger = {'schema': 'v3-issuer-last-observed-factors-0.1', 'market_asof': DAY, 'listed_total': 1,
              'investment_score_certified': False, 'mixed_date_score_research_only': True,
              'missing_is_not_zero': True, 'rows': {BASE['code']: {
                  **{k: BASE[k] for k in ('code', 'name', 'market')}, 'active': True,
                  'factors': {'valuation': {'points': 10, 'available': 20, 'asof': DAY,
                                             'source': 'current_close_replay'}},
                  'components': {'valuation': components}}}}
    return catalog, book, source, ledger


class EPSValuationTests(unittest.TestCase):
    def test_entire_saved_market_new_model_scores_and_ranks_equal_browser(self):
        def read(name):
            return json.loads((ROOT/'docs'/name).read_text())
        market, catalog, ledger = read('data.json'), read('v22-market-catalog.json'), read('v3-issuer-last-observed-factors.json')
        eps = ROOT/'docs/v3-dart-reported-eps.json'
        if not eps.is_file() or ledger['market_asof'] != catalog['date']:
            return  # The current-close publisher creates the next aligned ledger.
        overrides, _ = build(catalog, read('v3-dart-book-reference.json'),
                             json.loads(eps.read_text()), ledger)
        rows, summary = personal_build(market, catalog, ledger, valuation_overrides=overrides)
        keys = ('code', 'name', 'market', 'price_date', 'price', 'stop_line', 'stop_pct',
                'market_cap_eok', 'price60', 'risk_flags')
        browser = [{'r': {**{k:s.get(k) for k in keys},
                           'v3_last_known_factors': ledger['rows'][s['code']]['factors']}}
                   for s in market['stocks']]
        program = """
const fs=require('node:fs'),w=require('./docs/workspace.js');
const [rows,report,day]=JSON.parse(fs.readFileSync(0,'utf8'));
const applied=w.applyPersonalValuation({date:day,rows},report);
rows.sort(w.compareStableResearchScores);
console.log(JSON.stringify({applied,rows:rows.filter(x=>w.researchComposite(x.r).earned!==null).map((x,i)=>{
 const c=w.researchComposite(x.r),p=w.stabilityProfile(x.r,c);
 return [x.r.code,i+1,c.earned,c.available,p.score];
})}));
"""
        result = subprocess.run(['node', '-e', program], input=json.dumps([browser, summary, catalog['date']]),
                                text=True, capture_output=True, cwd=ROOT, check=True)
        js = json.loads(result.stdout)
        self.assertEqual(js['applied'], sum(code in {s['code'] for s in market['stocks']} for code in overrides))
        by = {row['code']: row for row in rows}
        self.assertEqual(len(js['rows']), summary['ranked_issuers'])
        for code, rank, earned, available, adjusted in js['rows']:
            row = by[code]
            self.assertEqual([rank, earned, available, adjusted],
                             [row['personal_rank'], row['base_score'], row['available'], row['adjusted_score']], code)

    def test_six_components_fill_exactly_twenty_and_preserve_original_source_points(self):
        catalog, book, source, ledger = fixture()
        before = copy.deepcopy((source, ledger))
        overrides, status = build(catalog, book, source, ledger)
        value = overrides[BASE['code']]
        self.assertEqual(sum(value['weights'].values()), 20)
        self.assertEqual(value['weights'], WEIGHTS)
        self.assertAlmostEqual(value['parts']['pbr'], 1.6)
        self.assertEqual(value['parts']['eps_growth'], 4)
        self.assertGreater(value['points'], 10)
        self.assertTrue(valid(value, code=BASE['code'], name=BASE['name'], market='KOSPI', day=DAY))
        self.assertEqual((source, ledger), before)

    def test_loss_zero_and_declining_eps_receive_no_positive_growth_reward(self):
        baseline = {'pbr': 2, 'earnings_yield_pct': 3, 'fcf_yield': 4, 'sales_to_market_cap': 1}
        for eps in ([0, 10, 20], [-5, 10, 30], [10, -1, 30], [30, 20, 10]):
            parts, _ = evaluate(eps, baseline)
            self.assertEqual(parts['eps_growth'], 0)
            if min(eps) <= 0:
                self.assertEqual(parts['eps_stability'], 0)
        self.assertEqual(evaluate([100, 100, 100], baseline)[0]['eps_stability'], 4)
        with self.assertRaises(ValueError):
            evaluate([100, None, 120], baseline)

    def test_missing_fcf_stale_component_or_unmatched_receipt_never_forces_new_model(self):
        for kind in ('missing_fcf', 'stale', 'receipt'):
            catalog, book, source, ledger = fixture()
            if kind == 'missing_fcf':
                del ledger['rows'][BASE['code']]['components']['valuation']['fcf_yield']
            if kind == 'stale':
                ledger['rows'][BASE['code']]['components']['valuation']['pbr']['asof'] = '2026-11-02'
            if kind == 'receipt':
                book['records'][0]['annual']['receipt_id'] = '20260401000001'
            with self.subTest(kind=kind):
                self.assertEqual(build(catalog, book, source, ledger)[0], {})

    def test_last_valid_new_model_keeps_its_date_when_source_is_missing(self):
        catalog, book, source, ledger = fixture()
        first, _ = build(catalog, book, source, ledger)
        catalog['date'] = '2026-11-04'
        kept, _ = build(catalog, None, None, ledger, first)
        self.assertEqual(kept[BASE['code']]['asof'], DAY)
        self.assertEqual(kept[BASE['code']]['points'], first[BASE['code']]['points'])
        self.assertTrue(kept[BASE['code']]['retained'])
        catalog['rows'][0]['name'] = 'Different issuer'
        self.assertEqual(build(catalog, None, None, ledger, first)[0], {})

    def test_tampered_score_or_certification_is_not_a_valid_cached_value(self):
        catalog, book, source, ledger = fixture()
        record = build(catalog, book, source, ledger)[0][BASE['code']]
        for key, value in (('points', 20), ('investment_score_certified', True),
                           ('eps', [100, None, 300]), ('asof', '2027-01-01'), ('retained', None)):
            bad = {**record, key: value}
            self.assertFalse(valid(bad, code=BASE['code'], name=BASE['name'], market='KOSPI', day=DAY))

    def test_browser_server_new_value_total_and_detail_agree(self):
        catalog, book, source, ledger = fixture()
        overrides, _ = build(catalog, book, source, ledger)
        stock = {**catalog['rows'][0], 'price_date': DAY, 'price': 100,
                 'stop_line': 94, 'stop_pct': -6, 'market_cap_eok': 5000,
                 'v3_last_known_factors': ledger['rows'][BASE['code']]['factors']}
        market = {'date': DAY, 'stocks': [stock]}
        rows, summary = personal_build(market, catalog, ledger, valuation_overrides=overrides)
        program = """
const fs=require('node:fs'),w=require('./docs/workspace.js');
const [r,report]=JSON.parse(fs.readFileSync(0,'utf8'));
const applied=w.applyPersonalValuation({date:r.price_date,rows:[{r}]},report);
const c=w.researchComposite(r),p=w.stabilityProfile(r,c);
const legacy={...r,code:'000001',name:'Other'};delete legacy.v3_personal_valuation;
const pair=w.resolveComparePair({date:r.price_date,rows:[{r},{r:legacy}]},r.code,legacy.code);
console.log(JSON.stringify({applied,points:c.earned,score:p.score,sameValuationModel:pair.sameValuationModel,detail:w.scorePillarExplanation(c.pillars.find(x=>x.key==='valuation'),r)}));
"""
        r = subprocess.run(['node', '-e', program], input=json.dumps([stock, summary]),
                           text=True, capture_output=True, cwd=ROOT, check=True)
        js = json.loads(r.stdout)
        self.assertEqual(js['applied'], 1)
        self.assertEqual(js['points'], rows[0]['base_score'])
        self.assertEqual(js['score'], rows[0]['adjusted_score'])
        self.assertFalse(js['sameValuationModel'])
        self.assertIn('가치20 개편', js['detail'])
        self.assertIn('EPS 성장', js['detail'])
        self.assertEqual(rows[0]['valuation_model'], MODEL)


if __name__ == '__main__':
    unittest.main()
