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

ROOT=Path(__file__).resolve().parents[1]


class OperationalScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names=['data.json','v22-market-catalog.json','v3-issuer-last-observed-factors.json',
            'v3-dart-bulk-history.json','v3-actual-money-participation.json','v3-personal-adjusted-score-summary.json','v3-dart-industry-classification.json']
        cls.inputs=[json.loads((ROOT/'docs'/n).read_text()) for n in names]
        cls.report=ops.build(*cls.inputs)

    def test_weights_missing_new_signals_and_no_cross_double_count(self):
        self.assertEqual(sum(ops.BUDGET.values()),100)
        self.assertEqual(len(self.report['rows']),self.inputs[1]['listed_total'])
        for r in self.report['rows'].values():
            self.assertNotIn('ma20_ma60_cross_near_ma240',r['parts']['volume'])
            # Saved November 4 market files predate the new slope receipt.
            self.assertIsNone(r['parts']['momentum']['daily_ma_slope_turn'])
            self.assertIsNone(r['parts']['momentum']['weekly_ma_slope_turn'])
            self.assertLessEqual(r['risk_deduction'],5)
            self.assertNotEqual(r['risk_status'],'safe')

    def test_score_first_ranking_and_csv_matches_every_row(self):
        ranked=sorted((r for r in self.report['rows'].values() if r['rank']),key=lambda r:r['rank'])
        self.assertEqual([r['score'] for r in ranked],sorted((r['score'] for r in ranked),reverse=True))
        records=list(csv.DictReader(StringIO(ops.to_csv(self.report))))
        self.assertEqual(len(records),len(self.report['rows']))
        for line in records:
            r=self.report['rows'][line['종목코드']]
            self.assertEqual(line['잠정운영점수'],'' if r['score'] is None else str(r['score']))
            self.assertEqual(line['모델버전'],ops.VERSION)

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

    def test_r1a_prior_receipts_migrate_without_relabeling_legacy_scores(self):
        old=json.loads((ROOT/'docs'/'v3-operational-research-scores.json').read_text())
        self.assertIn(old['version'],ops.COMPATIBLE_PREVIOUS)
        migrated=ops.build(*self.inputs,previous=old)
        self.assertEqual(migrated['version'],ops.VERSION)
        self.assertEqual(migrated['prior_operational_version'],old['version'])
        code='005930'
        self.assertEqual(migrated['rows'][code]['areas'],self.report['rows'][code]['areas'])
        self.assertEqual(migrated['rows'][code]['prior_operational_score'],old['rows'][code]['score'])
        records=list(csv.DictReader(StringIO(ops.to_csv(migrated))))
        self.assertEqual(next(r for r in records if r['종목코드']==code)['직전운영모델점수'],
                         str(old['rows'][code]['score']))

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
const ui=require('./docs/workspace.js'),core=require('./docs/v3-ranking.js');
const market=JSON.parse(fs.readFileSync('docs/data.json'));
const model=core.prepare(market,null,null,null,null,null);
if(!ops.attach(model,report))throw Error('actual model attachment');
const ranked=ui.rankItems(model,core,'all','all','','ops');
for(let i=1;i<ranked.length;i++)if((ranked[i-1].r.v3_operational?.score??-1)<(ranked[i].r.v3_operational?.score??-1))throw Error('actual screen order');
for(const x of ranked)if(x.r.v3_operational?.rank&&x.candidatePosition!==x.r.v3_operational.rank)throw Error('screen/csv rank identity');
"""
        subprocess.run(['node','-e',script],cwd=ROOT,check=True)
