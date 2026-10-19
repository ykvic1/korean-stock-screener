'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const m=require('../docs/v3-ranking.js');
const w={'거래량·OBV':20,'일봉 구조':20,'주봉·추세':15,
         '수급':20,'시장 대비 강도':10,'PBR':5,'실적·현금흐름':5,'위험·손익비':5};
const comps={'거래량·OBV':18,'일봉 구조':17,'주봉·추세':12,
         '수급':null,'시장 대비 강도':8,'PBR':null,
         '실적·현금흐름':null,'위험·손익비':3};
const raw=Math.round((18+17+12+8+3)/70*1000)/10;
const stocks=[
 {code:'111111',name:'초기 후보',market:'KOSPI',price:10000,
  stop_line:9000,stop_pct:-10,score:raw,coverage_pct:70,components:comps,
  price60:5,patterns:['역헤드앤숄더 후보'],phase:'상승 초기'},
 {code:'222222',name:'위험 급등',market:'KOSDAQ',price:21000,
  stop_line:19500,stop_pct:-7.14,score:raw,coverage_pct:70,components:comps,
  price60:44,risk_flags:['단기 급등 — 추격 주의'],phase:'추세 상승 중'},
 {code:'333333',name:'손절 미확인',market:'KOSDAQ',price:23000,
  stop_line:null,stop_pct:null,score:raw,coverage_pct:70,components:comps,
  phase:'장기 추세 전환 관찰'}
];
const snap={status:'success',version:'2.0',date:'2026-10-16',
  eligible_count:2498,weights:w,stocks};
const research={status:'exploratory',asof:'2026-10-16',stocks:[{
 code:'111111',status:'exploratory',reference_price_date:'2026-10-16',
 periods:{'스윙 1개월':{status:'exploratory',sample_n:113,symbol_n:18,quarter_n:16,
  mean_net_return_pct:5.2,stop_first_pct:18,max_adverse_p75_pct:12,
  up_pct:61,target_first_pct:49,
  chronological_holdout:{status:'미검증',test_mean_net_return_pct:null}}}
}]};
const recs={asof:'2026-10-16',research_source_matched:true,
  dossiers:[{code:'111111',qualification:'needs_deeper_evidence'}]};
assert.equal(m.observedScore(stocks[0],w),true);
assert.equal(m.stopDistance(stocks[0]),10);
assert.equal(m.stopDistance(stocks[2]),null);
assert.equal(m.highRisk(stocks[1]),true);
assert.equal(m.decision(stocks[1],research.stocks[0]).label,'매수 보류');
assert.equal(m.decision(stocks[2],research.stocks[0]).label,'매수 보류');
assert.equal(m.decision(stocks[0],research.stocks[0]).label,'조건부 모의 검토');
assert.equal(m.decision(stocks[0],null).label,'퀀트 대기');
const result=m.prepare(snap,research,recs);
assert.equal(result.marketEligible,2498);
assert.equal(result.rows.length,3);
assert.equal(result.quantCount,1);
assert.equal(result.recommendationCount,1);
assert.equal(result.recommendationQuantCount,1);
assert.equal(result.recommendationUsableQuantCount,1);
assert.equal(result.recommendationAwaitingCount,0);
assert.equal(result.rows[0].r.code,'111111');
assert.equal(m.select(result,'recommended').length,1);
assert.equal(m.select(result,'quant').length,1);
assert.equal(m.select(result,'early').length,1);
assert.equal(m.select(result,'all','KOSDAQ').length,2);
assert.equal(m.select(result,'all','all','위험').length,1);
assert.equal(m.period(research.stocks[0],'스윙 1개월').sample_n,113);
assert.equal(m.period(research.stocks[0],'중기 3개월'),null);
const weak=structuredClone(research.stocks[0]);
weak.periods['스윙 1개월'].symbol_n=1;
assert.equal(m.period(weak,'스윙 1개월'),null,'few symbol observations are not sufficient');
const weakQuarter=structuredClone(research.stocks[0]);
weakQuarter.periods['스윙 1개월'].quarter_n=3;
assert.equal(m.period(weakQuarter,'스윙 1개월'),null,'few distinct quarters are not sufficient');
const badHoldout=structuredClone(research);
badHoldout.asof='2026-10-15';
const stale=m.prepare(snap,badHoldout,recs);
assert.equal(stale.quantCount,0);
assert.equal(stale.recommendationQuantCount,0);
assert.equal(stale.recommendationAwaitingCount,1);
assert.equal(stale.rows[0].decision.label,'퀀트 대기');
const duplicate={...snap,stocks:[stocks[0],stocks[0]]};
assert.equal(m.prepare(duplicate,research,recs).rows.length,1);
const corrupt=structuredClone(stocks[0]);
corrupt.score=99;
assert.equal(m.prepare({...snap,stocks:[corrupt]},research,recs).rows.length,0);
const misleading=structuredClone(stocks[0]);
misleading.coverage_pct=100;
assert.equal(m.prepare({...snap,stocks:[misleading]},research,recs).rows.length,0);
const fake=structuredClone(stocks[0]);fake.stop_pct=-1;
assert.equal(m.stopDistance(fake),null);
const lq=structuredClone(research);lq.stocks[0].reference_price_date='2026-10-15';
assert.equal(m.prepare(snap,lq,recs).quantCount,0);
const badRec={...recs,asof:'2026-10-15'};
assert.equal(m.prepare(snap,research,badRec).recommendationCount,0);
assert.equal(m.esc('<img src=x>'),'&lt;img src=x&gt;');
const home=fs.readFileSync(path.join(__dirname,'../docs/v3-beta.html'),'utf8');
assert.ok(home.includes('자동 종목 추천 · 퀀트 검증'));
assert.ok(home.includes('v3-ranking.js')&&home.includes('v3-ranking.css'));
assert.ok(home.includes('심층 선별 후보')&&home.includes('퀀트 자료 있는 종목'));
assert.ok(home.includes('v3-manual.html'));
assert.ok(home.includes('rankCandidateQuant'));
assert.ok(!home.includes('수익률 100% 보장'));
console.log('v3 ranked beta: weighted legacy points, structural downside, quant date and no fake V3 promotion tests passed');
