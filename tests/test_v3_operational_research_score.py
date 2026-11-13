import copy
import csv
from io import StringIO
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import v3_operational_research_score as ops
from v3_actual_money_structure import SCHEMA as MONEY_SCHEMA
from v3_breakout_followthrough import observe as breakout_observe
from v3_krx_close_momentum_recovery import make_receipt, STOCK_FIELD
from v3_path_provisional_factors import preview as path_preview
from v3_unscored_path_observations import observe as observe_path
from datetime import date,timedelta

ROOT=Path(__file__).resolve().parents[1]


class OperationalScoreTests(unittest.TestCase):
    def test_official_close_path_adds_only_existing_momentum_weight(self):
        day = '2026-11-12'
        dates = [date.fromisoformat(day)-timedelta(days=i) for i in range(180)]
        dates = sorted(d for d in dates if d.weekday() < 5)[-121:]
        close = [100 + i for i in range(121)]
        pairs = list(zip(close, [1_000_000]*121))
        reason, receipt = make_receipt(pairs, asof=day)
        self.assertEqual(reason, 'recovered')
        stock = {'price_date': day, 'market': 'KOSPI', 'code': '000001'}
        ordinary = observe_path(close, [100]*121, asof=day, session_days=dates)
        expected = path_preview(dict(stock, v3_unscored_path_observations=ordinary), asof=day)
        saved = {'factors': {'momentum': {'asof': day, 'available': 8, 'points': 4}}}
        parts, _ = ops.score_issuer(dict(stock, **{STOCK_FIELD: receipt}), saved, day=day)
        self.assertEqual(parts['momentum']['existing_momentum'],
                         {'asof': day, 'available': 15,
                          'points': 4+expected['research_momentum_additional_points']})
        self.assertIsNone(parts['volume']['post_spike_absorption'])
        for bad in (pairs[:-1], [None, *pairs[1:]], [(100, 2_000_000), *pairs[1:]],
                    [(1000, 1_000_000), *pairs[1:]]):
            self.assertIsNone(make_receipt(bad, asof=day)[1])
        saved['factors']['momentum']['asof'] = '2026-11-11'
        parts, _ = ops.score_issuer(dict(stock, **{STOCK_FIELD: receipt}), saved, day=day)
        self.assertEqual(parts['momentum']['existing_momentum']['available'], 8)

    def test_independent_weekly_rsi_observes_only_its_existing_weight(self):
        day = '2026-11-12'
        receipt = {'asof': '2026-11-06', 'rsi14': 45.,
                   'rsi30_cross_recent': True, 'rsi_bullish_divergence_recent': False}
        stock = {'price_date': day, 'market': 'KOSPI', 'code': '000001',
                 'v3_weekly_rsi_gap_recovery': receipt}
        parts, _ = ops.score_issuer(stock, {}, day=day)
        self.assertEqual(parts['technical']['weekly_rsi'],
                         {'asof': '2026-11-06', 'available': 1.8, 'points': 1.8})
        self.assertIsNone(parts['technical']['weekly_pattern'])
        receipt['rsi30_cross_recent'] = False
        parts, _ = ops.score_issuer(stock, {}, day=day)
        self.assertEqual(parts['technical']['weekly_rsi']['points'], 0)
        self.assertEqual(parts['technical']['weekly_rsi']['available'], 1.8)
        for change in ({'asof': '2026-10-30'}, {'asof': '2026-11-13'}, {'rsi14': 101},
                       {'rsi30_cross_recent': 1}):
            stock['v3_weekly_rsi_gap_recovery'] = {**receipt, **change}
            parts, _ = ops.score_issuer(stock, {}, day=day)
            self.assertIsNone(parts['technical']['weekly_rsi'])

    def test_name_change_retention_requires_same_close_and_unchanged_filing_sources(self):
        day='2026-11-06';code='148250'
        member={'market':'KOSDAQ','name':'new label'}
        prior={'market':'KOSDAQ','name':'old label'}
        hashes={'v3-dart-bulk-history.json':'a'*64,'v3-dart-book-reference.json':'b'*64}
        previous={'asof':day,'source_hashes':hashes.copy()}
        annual={'code':code,'status':'observed','annual':{'year':2025,'fs_div':'CFS','receipt_id':'20260323001654'}}
        book={'records':[annual]}
        history={'records':{code:{'years':{'2025':{'CFS':{'receipt_id':'20260323001654'}}}}}}
        def valid(prev=previous,current=hashes,reference=book,archive=history,old=prior):
            return ops.unchanged_fiscal_identity(code,member,old,day,prev,current,reference,archive)
        self.assertTrue(valid())
        self.assertFalse(valid(prev={**previous,'asof':'2026-11-05'}))
        self.assertFalse(valid(current={**hashes,'v3-dart-bulk-history.json':'c'*64}))
        self.assertFalse(valid(old={**prior,'market':'KOSPI'}))
        self.assertFalse(valid(reference={'records':[]}))
        changed=copy.deepcopy(history);changed['records'][code]['years']['2025']['CFS']['receipt_id']='20260323001655'
        self.assertFalse(valid(archive=changed))

    @classmethod
    def setUpClass(cls):
        names=['data.json','v22-market-catalog.json','v3-issuer-last-observed-factors.json',
            'v3-dart-bulk-history.json','v3-actual-money-participation.json','v3-personal-adjusted-score-summary.json','v3-dart-industry-classification.json',
            'v3-dart-book-reference.json','v3-dart-reported-eps.json']
        cls.inputs=[json.loads((ROOT/'docs'/n).read_text()) for n in names]
        cls.report=ops.build(*cls.inputs)

    def test_same_day_partial_eps_refresh_retains_previous_complete_factor(self):
        previous=json.loads((ROOT/'docs'/ops.OUTPUT).read_text())
        # These complete factors were admitted from audited original EPS.
        # A temporary absence while the reader refreshes must keep their date.
        with patch.object(ops,'verified_reported_eps',return_value={}):
            report=ops.build(*self.inputs,previous=previous)
        for code in ('011210','039030'):
            old=previous['rows'][code]['parts']['valuation']['per_eps_quality_growth']
            expected=old if isinstance(old,list) else [old['points'],old['available'],old['asof']]
            result=report['rows'][code]['parts']['valuation']['per_eps_quality_growth']
            self.assertEqual(result['available'],expected[1])
            self.assertEqual(result['asof'],expected[2])

    def test_new_relative_momentum_only_retains_prior_complete_momentum(self):
        previous = json.loads((ROOT/'docs'/ops.OUTPUT).read_text())
        ledger = copy.deepcopy(self.inputs[2])
        code = next(code for code,row in previous['rows'].items()
                    if row['parts']['momentum'].get('existing_momentum', [None,0])[1]==15
                    and 'momentum' in ledger['rows'][code]['factors'])
        old = previous['rows'][code]['parts']['momentum']['existing_momentum']
        previous['rows'][code]['parts']['momentum']['existing_momentum'] = [
            old[0], 15, (date.fromisoformat(previous['asof'])-timedelta(days=1)).isoformat()]
        ledger['rows'][code]['factors']['momentum'].update(
            points=4, available=8, asof=previous['asof'])
        updated = ops.build(*self.inputs[:2], ledger, *self.inputs[3:], previous=previous)
        result = updated['rows'][code]['parts']['momentum']['existing_momentum']
        self.assertEqual([result['points'],result['available'],result['asof']],
                         previous['rows'][code]['parts']['momentum']['existing_momentum'])

    def test_weights_missing_new_signals_and_no_cross_double_count(self):
        self.assertEqual(sum(ops.BUDGET.values()),100)
        self.assertEqual(len(self.report['rows']),self.inputs[1]['listed_total'])
        for r in self.report['rows'].values():
            self.assertNotIn('ma20_ma60_cross_near_ma240',r['parts']['volume'])
            # The public market snapshot may advance after the next full scan.
            # Both genuinely missing and valid newly observed receipts must pass.
            for key,maximum in [('daily_ma_slope_turn',2),('weekly_ma_slope_turn',3)]:
                receipt=r['parts']['momentum'][key]
                if receipt is not None:
                    self.assertGreater(receipt['available'],0)
                    self.assertLessEqual(receipt['available'],maximum)
                    self.assertLessEqual(receipt['points'],receipt['available'])
            breakout=r['parts']['technical']['independent_breakout_confirmation']
            if breakout is not None:
                self.assertEqual(breakout['available'],1.2)
                self.assertIn(breakout['points'],(0,.6,1.2))
            self.assertLessEqual(r['risk_deduction'],5)
            self.assertNotEqual(r['risk_status'],'safe')

    def test_new_partial_ma_receipt_does_not_erase_prior_full_observation(self):
        previous=json.loads((ROOT/'docs'/ops.OUTPUT).read_text())
        market=copy.deepcopy(self.inputs[0])
        stock=next(stock for stock in market['stocks']
                   if stock.get('v3_ma_slope_transition',{}).get('parts',{}).get('weekly') is not None
                   and previous['rows'][stock['code']]['parts']['momentum'].get('weekly_ma_slope_turn'))
        code=stock['code']
        stock['v3_ma_slope_transition']['parts']['weekly']['120']=None
        partial,_=ops.score_issuer(stock,self.inputs[2]['rows'][code],day=market['date'])
        self.assertLess(partial['momentum']['weekly_ma_slope_turn']['available'],3)
        recovered=ops.build(*([market]+self.inputs[1:]),previous=previous)
        new=recovered['rows'][code]['parts']['momentum']['weekly_ma_slope_turn']
        old=previous['rows'][code]['parts']['momentum']['weekly_ma_slope_turn']
        self.assertEqual(new['available'],old[1])
        self.assertEqual(new['asof'],old[2])

    def test_ma240_receipt_survives_independent_volume_adapter_rejection(self):
        from v3_ma240_cross_research import observe as ma_observe
        end=date(2026,11,6)
        dates=[]
        day=end
        while len(dates)<260:
            if day.weekday()<5:dates.append(day)
            day-=timedelta(days=1)
        dates.reverse()
        cross=ma_observe([100.0]*260,[1000]*260,
                         asof=end.isoformat(),session_days=dates)
        self.assertIsNotNone(cross)
        stock={'market':'KOSPI','price_date':end.isoformat(),
               'volume_ratio':109.43,'obv_rising':False,
               'divergence':False,'price60':-62.0,
               'v3_ma240_cross_research':cross}
        saved={'code':'250030','name':'hypothetical','market':'KOSPI'}
        parts,_=ops.score_issuer(stock,saved,day=end.isoformat())
        self.assertEqual(parts['technical']['ma20_ma60_cross_near_ma240'],{
            'points':0,'available':6,'asof':end.isoformat()})
        self.assertIsNone(parts['volume']['obv_price_divergence'])
        forged={**cross,'certified_investment_points':6}
        stock['v3_ma240_cross_research']=forged
        rejected,_=ops.score_issuer(stock,saved,day=end.isoformat())
        self.assertIsNone(rejected['technical']['ma20_ma60_cross_near_ma240'])

    def test_score_first_ranking_and_csv_matches_every_row(self):
        ranked=sorted((r for r in self.report['rows'].values() if r['rank']),key=lambda r:r['rank'])
        self.assertEqual([r['score'] for r in ranked],sorted((r['score'] for r in ranked),reverse=True))
        records=list(csv.DictReader(StringIO(ops.to_csv(self.report))))
        self.assertEqual(len(records),len(self.report['rows']))
        for line in records:
            r=self.report['rows'][line['종목코드']]
            self.assertEqual(line['잠정운영점수'],'' if r['score'] is None else str(r['score']))
            self.assertEqual(line['모델버전'],ops.VERSION)

    def test_lightweight_r1d_coverage_meter_preserves_missing_weight(self):
        meter=ops.coverage_summary(self.report,'a'*64)
        rows=list(self.report['rows'].values())
        self.assertEqual(meter['listed_total'],len(rows))
        self.assertEqual(meter['ranked_count'],sum(r['rank'] is not None for r in rows))
        for field,threshold in [('available_100_count',100),
                                ('available_95_count',95),('available_90_count',90)]:
            self.assertEqual(meter[field],sum(r['available']>=threshold for r in rows))
        self.assertEqual(meter['budgets'],ops.BUDGET)
        self.assertFalse(meter['investment_score_certified'])
        self.assertFalse(meter['performance_validated'])
        self.assertTrue(meter['missing_is_not_zero'])
        self.assertEqual(meter['provider_calls'],0)

    def test_known_risk_lower_bound_is_continuous_and_never_claims_safe(self):
        def record(equity,loss_years):
            return {'years':{str(2022+i):{'CFS':{'values':{'assets':100,'equity':equity,
                'revenue':100,'operating_income':-30 if i>=4-loss_years else 10}}} for i in range(4)}}
        low=ops.risk_assessment(record(40,1));high=ops.risk_assessment(record(-1,4))
        self.assertGreater(high['deduction'],low['deduction'])
        self.assertEqual(high['deduction'],5)
        a=ops.risk_assessment(record(21.99,0))['deduction']
        b=ops.risk_assessment(record(22.01,0))['deduction']
        self.assertLess(abs(a-b),.01)
        bank=ops.risk_assessment(record(8,0),financial_sector=True)
        unknown=ops.risk_assessment(record(8,0),financial_sector=None)
        self.assertEqual(bank['deduction'],0)
        self.assertEqual(unknown['deduction'],0)

    def test_source_identity_future_and_missing_evidence(self):
        changed=copy.deepcopy(self.inputs)
        changed[3]['asof']='2099-01-01'
        with self.assertRaises(ValueError):ops.build(*changed)
        changed=copy.deepcopy(self.inputs)
        member=changed[1]['rows'][0]
        changed[2]['rows'][member['code']]['name']='different issuer'
        with self.assertRaises(ValueError):ops.build(*changed)
        parts,risk=ops.score_issuer(None,{'code':'000001','name':'x','market':'KOSPI'},day='2026-11-04')
        self.assertTrue(all(p is None for ps in parts.values() for p in ps.values()))
        self.assertEqual(risk['status'],'unknown')

    def test_new_market_day_does_not_destroy_prior_scores_during_ledger_lag(self):
        previous=json.loads((ROOT/'docs'/'v3-operational-research-scores.json').read_text())
        self.assertIn(previous['version'],ops.COMPATIBLE_PREVIOUS)
        market,catalog,ledger,history=copy.deepcopy(self.inputs[:4])
        market['date']=(date.fromisoformat(catalog['date'])+timedelta(days=1)).isoformat()
        self.assertTrue(ops.can_hold_previous_during_temporal_lag(
            market,catalog,ledger,history,previous))
        with self.assertRaisesRegex(ValueError,'unaligned_operational_sources'):
            ops.build(*[market,catalog,ledger,history]+self.inputs[4:])
        broken=copy.deepcopy(previous);broken['investment_score_certified']=True
        self.assertFalse(ops.can_hold_previous_during_temporal_lag(
            market,catalog,ledger,history,broken))
        broken=copy.deepcopy(ledger);broken['missing_is_not_zero']=False
        self.assertFalse(ops.can_hold_previous_during_temporal_lag(
            market,catalog,broken,history,previous))

    def test_prior_receipts_migrate_without_relabeling_legacy_scores(self):
        old=json.loads((ROOT/'docs'/'v3-operational-research-scores.json').read_text())
        self.assertIn(old['version'],ops.COMPATIBLE_PREVIOUS)
        migrated=ops.build(*self.inputs,previous=old)
        self.assertEqual(migrated['version'],ops.VERSION)
        previous_version=(old.get('prior_operational_version') if old['version']==ops.VERSION
                          else old['version'])
        self.assertEqual(migrated['prior_operational_version'],previous_version)
        code='005930'
        self.assertEqual(migrated['rows'][code]['areas'],self.report['rows'][code]['areas'])
        previous_score=(old['rows'][code].get('prior_operational_score') if old['version']==ops.VERSION
                        else old['rows'][code]['score'])
        self.assertEqual(migrated['rows'][code]['prior_operational_score'],previous_score)
        records=list(csv.DictReader(StringIO(ops.to_csv(migrated))))
        self.assertEqual(next(r for r in records if r['종목코드']==code)['직전운영모델점수'],
                         '' if previous_score is None else str(previous_score))

    def test_r1c_money_structure_requires_independent_valid_receipt(self):
        structure={'schema':MONEY_SCHEMA,'growth6':4.2,'components':{
            'persistence':3.1,'concentration_quality':1.1,'up_down_asymmetry':.6,
            'correction_contraction':.3,'weekly_structure_increment':.8},
            'derived_from_private_reported_acc_trdval':True,
            'original_amounts_or_paths_exported':False,
            'intrabucket_boundary_price_sign_known':False,
            'independent_regular_session_verified':False,'investment_score_certified':False}
        money={'asof':'2026-11-04','r1c_structure':structure}
        stub={'subpoints':{'sustained_volume':4.9,'obv_price_not_reflected':2.4,
            'ma20_ma60_cross_near_ma240':None}}
        stock={'price_date':'2026-11-04','market':'KOSPI'}
        saved={'code':'005930','name':'삼성전자','market':'KOSPI'}
        with patch.object(ops,'volume_observed',return_value=stub),patch.object(ops,'path_preview',return_value=None),\
             patch.object(ops,'pattern_observed',return_value=None):
            parts,_=ops.score_issuer(stock,saved,day='2026-11-04',money=money)
            self.assertEqual(parts['volume']['long_horizon_growth']['points'],4.2)
            self.assertEqual(parts['volume']['persistence']['points'],3.1)
            self.assertEqual(parts['volume']['concentration_quality']['points'],1.1)
            self.assertEqual(parts['volume']['weekly_structure_increment']['points'],.8)
            self.assertEqual(parts['volume']['obv_price_divergence']['points'],2.4)
            corrupt=copy.deepcopy(money);corrupt['r1c_structure']['investment_score_certified']=True
            fallback,_=ops.score_issuer(stock,saved,day='2026-11-04',money=corrupt)
            self.assertEqual(fallback['volume']['long_horizon_growth']['points'],4.9)
            self.assertIsNone(fallback['volume']['persistence'])

    def test_recovered_weekly_position_fills_only_existing_long_ma_component(self):
        from v3_operational_research_score import score_issuer

        day = '2026-11-12'
        stock = {'price_date': day, 'market': 'KOSPI',
                 'v3_weekly_position_gap_recovery': {
                     'asof': '2026-11-06', 'weekly_ma120_gap_pct': 5.}}
        parts, _ = score_issuer(stock, {}, day=day)
        self.assertEqual(parts['technical']['long_ma_position'],
                         {'points': 1.8, 'available': 1.8, 'asof': '2026-11-06'})
        self.assertIsNone(parts['technical']['weekly_pattern'])
        self.assertIsNone(parts['technical']['weekly_rsi'])
        for wrong_day in ('2026-11-13', '2026-10-30', '2026-11-10', None):
            stock['v3_weekly_position_gap_recovery']['asof'] = wrong_day
            parts, _ = score_issuer(stock, {}, day=day)
            self.assertIsNone(parts['technical']['long_ma_position'])

    def test_price_only_breakout_confirmation_is_fifth_independent_observation(self):
        day='2026-11-04';last=date.fromisoformat(day)
        receipt=breakout_observe([100.]*82+[104.]*18,asof=day,
            session_days=[last-timedelta(days=99-i) for i in range(100)])
        stock={'price_date':day,'market':'KOSPI','v3_breakout_followthrough':receipt}
        saved={'code':'005930','name':'삼성전자','market':'KOSPI'}
        with patch.object(ops,'volume_observed',return_value=None),patch.object(ops,'path_preview',return_value=None),\
             patch.object(ops,'pattern_observed',return_value=None):
            parts,_=ops.score_issuer(stock,saved,day=day)
            self.assertEqual(parts['technical']['independent_breakout_confirmation']['points'],1.2)
            self.assertEqual(parts['technical']['independent_breakout_confirmation']['available'],1.2)
            corrupt=copy.deepcopy(stock)
            corrupt['v3_breakout_followthrough']['source_regular_session_verified']=True
            invalid,_=ops.score_issuer(corrupt,saved,day=day)
            self.assertIsNone(invalid['technical']['independent_breakout_confirmation'])

    def test_reported_eps_valuation_does_not_require_unrelated_fcf_or_pbr(self):
        # Fixed source fixture: a live annual book can advance before the
        # independently published EPS digest. That lag must not break CI.
        from test_v3_dart_reported_eps import inputs, payload, BASE, DAY
        from v3_dart_reported_eps import parse, snapshot
        catalog,book=inputs()
        record=parse(payload(),corp='00123456',base=BASE,on_day=DAY)
        source=snapshot(DAY,catalog,book,{BASE['code']:BASE},{BASE['code']:record})
        verified=ops.verified_reported_eps(catalog,book,source)
        self.assertEqual(set(verified),{BASE['code']})
        code=BASE['code'];member=catalog['rows'][0]
        # A verified source can observe EPS growth+stability with absolutely
        # no price/money/FCF/PBR baseline. The remaining 1/3 stays missing.
        stub={'code':code,'name':member['name'],'market':member['market']}
        parts,_=ops.score_issuer(None,stub,day=catalog['date'],reported_eps=verified[code])
        eps=parts['valuation']['per_eps_quality_growth']
        self.assertIsNotNone(eps)
        self.assertAlmostEqual(eps['available'],10/3,places=5)
        self.assertLessEqual(eps['points'],eps['available'])
        self.assertIsNone(parts['valuation']['fcf_yield'])
        self.assertIsNone(parts['valuation']['pbr_equity_quality'])
        future=copy.deepcopy(stub)
        future['components']={'valuation':{'earnings_yield_pct':
            {'available':5,'points':5,'asof':'2099-01-01'}}}
        safe,_=ops.score_issuer(None,future,day=catalog['date'],reported_eps=verified[code])
        self.assertAlmostEqual(safe['valuation']['per_eps_quality_growth']['available'],10/3,places=5)
        tampered=copy.deepcopy(source)
        tampered['source_sha256']['annual_book']='0'*64
        self.assertEqual(ops.verified_reported_eps(catalog,book,tampered),{})
        tampered=copy.deepcopy(source)
        tampered['records'][code]['metrics']['complete_three_years']=False
        self.assertEqual(ops.verified_reported_eps(catalog,book,tampered),{})

    def test_new_verified_eps_weight_not_lost_to_later_dated_partial_prior(self):
        older=json.loads((ROOT/'docs'/'v3-operational-research-scores.json').read_text())
        updated=ops.build(*self.inputs,previous=older)
        for code,r in self.report['rows'].items():
            fresh=r['parts']['valuation']['per_eps_quality_growth']
            saved=updated['rows'][code]['parts']['valuation']['per_eps_quality_growth']
            if fresh:
                self.assertIsNotNone(saved)
                self.assertGreaterEqual(saved['available']+1e-6,fresh['available'])

    def test_browser_csv_server_and_corruption_rejection(self):
        script="""
const fs=require('fs'),ops=require('./docs/operational-score.js');
const report=JSON.parse(fs.readFileSync('docs/v3-operational-research-scores.json'));
const rows=Object.entries(report.rows).map(([code,v])=>({r:{code,name:v.name,market:v.market}}));
if(ops.attach({rows,date:report.asof},report)!==rows.length)throw Error('attachment');
const sorted=rows.filter(x=>x.r.v3_operational.score!==null).sort(ops.compare);
for(let i=0;i<sorted.length;i++)if(sorted[i].r.v3_operational.rank!==i+1)throw Error('rank parity');
const corrupt=structuredClone(report);corrupt.rows[Object.keys(corrupt.rows)[0]].score=99;
if(ops.attach({rows,date:report.asof},corrupt)!==0)throw Error('corruption');
if(ops.markup(sorted[0].r).includes('undefined'))throw Error('markup');
for(const {r} of rows){
 const gaps=ops.missingComponents(r.v3_operational);
 const remainder=gaps.reduce((sum,item)=>sum+item.missing,0);
 if(Math.abs(remainder-(100-r.v3_operational.available))>0.00002)
  throw Error('missing component weight must reconcile with available');
 if(gaps.some(item=>item.missing<=0||!Number.isFinite(item.missing)))
  throw Error('invalid missing weight');
}
const incomplete=rows.find(x=>x.r.v3_operational.available>0&&x.r.v3_operational.available<100);
if(!incomplete||!ops.markup(incomplete.r).includes('미관측 항목')||
   !ops.markup(incomplete.r).includes('미관측은 0점이 아닙니다'))
 throw Error('missing evidence explanation absent');
const complete=rows.find(x=>x.r.v3_operational.available===100);
if(!complete||!ops.markup(complete.r).includes('근거가 모두 관측되었습니다')||
   ops.markup(complete.r).includes('미관측 항목'))
 throw Error('fully observed row mislabeled missing');
const split=ops.missingComponents({parts:{valuation:{per_eps_quality_growth:[.5,5/3,report.asof]}}});
const epsGap=split.find(item=>item.key==='per_eps_quality_growth');
if(!epsGap||!epsGap.partiallyObserved||Math.abs(epsGap.missing-10/3)>.00002)
 throw Error('partial EPS receipt remaining weight hidden');
const ui=require('./docs/workspace.js'),core=require('./docs/v3-ranking.js');
const market=JSON.parse(fs.readFileSync('docs/data.json'));
const model=core.prepare(market,null,null,null,null,null);
const priced=model.rows.length;
const linked=ops.attachAllListed(model,report);
if(linked.linked!==report.listed_total||linked.unpriced!==report.listed_total-priced)
 throw Error('all-listed operational attachment');
if(model.rows.length!==report.listed_total)throw Error('all-listed count');
for(const x of model.rows.filter(x=>x.r.unpriced_operational_only)){
 if(x.r.price!==null||x.r.price_date!==null||x.r.score!==null||
    x.r.v3_operational!==report.rows[x.r.code])throw Error('fabricated unpriced observation');
}
const ranked=ui.rankItems(model,core,'all','all','','ops');
if(ranked.length!==report.listed_total)throw Error('all-listed search');
for(let i=1;i<ranked.length;i++)if((ranked[i-1].r.v3_operational?.score??-1)<(ranked[i].r.v3_operational?.score??-1))throw Error('actual screen order');
for(const x of ranked)if(x.r.v3_operational?.rank&&x.candidatePosition!==x.r.v3_operational.rank)throw Error('screen/csv rank identity');
const invalid=core.prepare(market,null,null,null,null,null);
const impostor=structuredClone(report);
impostor.rows[invalid.rows[0].r.code].name='changed issuer';
const mismatchedCode=invalid.rows[0].r.code;
const isolation=ops.attachAllListed(invalid,impostor);
if(isolation.linked!==report.listed_total-1||isolation.blocked!==1||
   isolation.unpriced!==report.listed_total-priced||invalid.rows.length!==report.listed_total)
 throw Error('identity mismatch must isolate only the disputed issuer');
const isolated=invalid.rows.find(x=>x.r.code===mismatchedCode);
if(!isolated.r.v3_operational_identity_blocked||isolated.r.v3_operational||
   !ops.markup(isolated.r).includes('불일치'))throw Error('unsafe score attributed to disputed identity');
if(invalid.rows.filter(x=>x.r.v3_operational).length!==report.listed_total-1)
 throw Error('other valid issuers lost their operational scores');
const mismatched=core.prepare(market,null,null,null,null,null);
mismatched.marketEligible++;
const rollover=ops.attachAllListed(mismatched,report);
if(rollover.linked!==priced||rollover.unpriced!==0||!rollover.stale||
   mismatched.rows.length!==priced)
 throw Error('changed roster must preserve only dated evidence for matching priced issuers');
const newer=core.prepare(market,null,null,null,null,null);
newer.date=new Date(Date.parse(report.asof+'T00:00:00Z')+86400000).toISOString().slice(0,10);
const older=ops.attachAllListed(newer,report);
if(older.linked!==priced||older.unpriced!==0||!older.stale||newer.rows.length!==priced)
 throw Error('previous-day report must not restore unknown delistings');
const changed=core.prepare(structuredClone(market),null,null,null,null,null);
changed.rows[0].r.code='999999';
const replaced=ops.attachAllListed(changed,report);
if(replaced.linked!==priced-1||replaced.missing!==1||replaced.unpriced!==0||
   !replaced.stale||changed.rows.length!==priced||
   !changed.rows[0].r.v3_operational_evidence_missing||changed.rows[0].r.v3_operational||
   !ops.markup(changed.rows[0].r).includes('저장본'))
 throw Error('new issuer must not be assigned a delisted issuer score');
"""
        subprocess.run(['node','-e',script],cwd=ROOT,check=True)

    def test_operational_score_refreshes_immediately_on_completed_source_jobs(self):
        workflow=(ROOT/'.github/workflows/v3-operational-research-score.yml').read_text()
        self.assertIn('- Full-Market 100-Stock Batches',workflow)
        self.assertIn('- V3 Actual Money Personal Participation Score',workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",workflow)
        self.assertIn('provider calls or collection locks',workflow)
