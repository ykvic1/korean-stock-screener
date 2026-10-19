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
const official={asof:'2026-10-16',dart:{status:'observed',stocks:[
 {code:'111111',status:'observed',reports:[
  {year:2025,fs_div:'CFS',receipt_id:'20260331001234',currency:'KRW',
   values:{revenue:12000000000,operating_income:900000000,
           operating_cashflow:1000000000,debt_to_equity_pct:90.4},
   revision_history_verified:false}
 ]},
 {code:'222222',status:'unconfirmed',reports:[]},
]}};
const withFinance=m.prepare(snap,research,recs,official);
assert.equal(withFinance.financialReferenceCount,1);
assert.equal(withFinance.recommendationFinancialCount,1);
assert.equal(withFinance.rows[0].financialRef.year,2025);
assert.equal(withFinance.rows[0].financialRef.values.revenue,12000000000);
assert.equal(m.eok(12000000000),'120억원');
assert.equal(m.eok(null),'미확인');
assert.equal(m.prepare(snap,research,recs,{...official,asof:'2026-10-15'}).financialReferenceCount,0,
 'old annual summary must not attach to newer stock price date');
assert.equal(m.observedAnnuals({...official,dart:{status:'key_required'}},'2026-10-16').size,0,
 'DART key missing is not equivalent to 0 KRW revenue');
assert.equal(m.observedAnnuals({...official,dart:{...official.dart,stocks:[
 {code:'111111',status:'observed',reports:[{...official.dart.stocks[0].reports[0],year:2027}]}]
}},'2026-10-16').size,0,'financial report newer than screen must not attach');
const priorityFeed={schema:'v3-targeted-live-shortlist-quant-v1',
 asof:'2026-10-16',status:'exploratory',peer_cohort_valid:29,
 raw_ohlcv_copied:false,verified_predictive_accuracy:false,
 stocks:[{code:'111111',status:'exploratory',reference_price_date:'2026-10-16',
         peer_cohort_requested:40,periods:{
          '스윙 1개월':research.stocks[0].periods['스윙 1개월']}}]};
const oldBroad={status:'exploratory',asof:'2026-10-15',stocks:[]};
const targeted=m.prepare(snap,oldBroad,recs,undefined,priorityFeed);
assert.equal(targeted.recommendationQuantCount,1);
assert.equal(targeted.recommendationUsableQuantCount,1);
assert.equal(targeted.rows[0].quantSource,'추천 우선 연구 40종목 요청 / 미확인종목 실제 이력 확보');
assert.equal(targeted.rows[0].decision.label,'조건부 모의 검토');
assert.equal(m.prepare(snap,research,recs,undefined,priorityFeed).rows[0].quantSource,
 '일반 연구 최대 120종목 표본','prefer same-day full 120 peer study when usable');
assert.equal(m.prepare(snap,oldBroad,recs,undefined,
 {...priorityFeed,asof:'2026-10-15'}).recommendationQuantCount,0);
assert.equal(m.prepare(snap,oldBroad,recs,undefined,
 {...priorityFeed,verified_predictive_accuracy:true}).recommendationQuantCount,0);
// Current, published dossiers are displayed even when a name is caution-only.
// A higher *observed* v2 score must never override a current risk warning.
const riskDossiers={asof:'2026-10-16',research_source_matched:false,dossiers:[
 {code:'222222',qualification:'separate_caution_research_only'},
 {code:'111111',qualification:'needs_deeper_evidence'}]};
const mixed=m.prepare(snap,oldBroad,riskDossiers,undefined,priorityFeed);
assert.equal(mixed.recommendationCount,2,'show all published research dossiers, including cautions');
assert.equal(mixed.recommendationCautionCount,1);
assert.equal(mixed.recommendationQuantCount,1);
assert.equal(mixed.recommendationUsableQuantCount,1);
assert.equal(mixed.recommendationInsufficientCount,0);
assert.deepEqual(m.select(mixed,'recommended').map(x=>x.r.code),['111111','222222'],
 'risk-reviewed shortlist first; cautionary scored names still visible separately');
assert.equal(mixed.rows.find(x=>x.r.code==='222222').decision.label,'매수 보류');
assert.equal(mixed.priorityQuantRequested,40);
assert.equal(mixed.priorityQuantValid,29);
const real120={...priorityFeed,peer_cohort_requested:120,peer_cohort_valid:75,
 stocks:priorityFeed.stocks.map(x=>({...x,peer_cohort_requested:120,peer_cohort_valid:75}))};
const expanded=m.prepare(snap,oldBroad,riskDossiers,undefined,real120);
assert.equal(expanded.priorityQuantRequested,120);
assert.equal(expanded.priorityQuantValid,75);
assert.ok(expanded.rows.find(x=>x.r.code==='111111').quantSource.includes('120종목 요청 / 75종목'));
assert.ok(m.quantGlance(real120.stocks[0]).includes('1개월 평균 +5.2%'));
// Real 2026-10-19 Avatec-like risk mix: a weakly positive 3m analogue
// does not outweigh consistently >=40% stop-first readings.
const avatecRisk={status:'exploratory',periods:{}};
for(const [key,mean,stop] of [
 ['스윙 1개월',-0.12,44.8],['스윙 2개월',-0.47,48.8],
 ['중기 3개월',0.77,46.2],['중기 6개월',-0.65,55.1]]){
 avatecRisk.periods[key]={status:'exploratory',sample_n:181,symbol_n:23,quarter_n:26,
  mean_net_return_pct:mean,stop_first_pct:stop,max_adverse_p75_pct:12};
}
assert.equal(m.decision(stocks[0],avatecRisk).label,'매수 보류',
 'do not buy a 70-plus point candidate when observed stop-first risks dominate');
assert.ok(m.quantGlance(avatecRisk).includes('1개월 평균 -0.1%'));
const insufficientStudy={status:'insufficient',periods:{}};
const insufficientPriority={...real120,stocks:[
 {...real120.stocks[0],code:'222222',status:'insufficient',periods:{}},
 real120.stocks[0]
]};
const insufficientView=m.prepare(snap,oldBroad,riskDossiers,undefined,insufficientPriority);
assert.equal(insufficientView.recommendationInsufficientCount,1);
assert.equal(insufficientView.recommendationAwaitingCount,0);
assert.equal(insufficientView.rows.find(x=>x.r.code==='222222').decision.label,'매수 보류');
assert.equal(m.prepare(snap,oldBroad,riskDossiers,undefined,
 {...insufficientPriority,asof:'2026-10-15'}).recommendationQuantCount,0,
 'never attach stale studied outcomes to a current-day shortlisted name');
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
const todayRecPending={...recs,research_source_matched:false,
  research_source_date:'2026-10-15'};
const pending=m.prepare(snap,oldBroad,todayRecPending);
assert.equal(pending.recommendationCount,1,
  'show current-day screened candidate without waiting for its historical quant');
assert.equal(pending.recommendationQuantCount,0,
  'old 120-stock study must never become a current-day quant outcome');
assert.equal(pending.rows[0].decision.label,'퀀트 대기',
  'allow research request but not an imaginary current-day buy signal');
const nowResearched=m.prepare(snap,oldBroad,todayRecPending,undefined,priorityFeed);
assert.equal(nowResearched.recommendationUsableQuantCount,1,
  'a separately observed same-date priority cohort must bootstrap current candidate review');
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
const uiSource=fs.readFileSync(path.join(__dirname,'../docs/v3-ranking.js'),'utf8');
assert.ok(!uiSource.includes('40종목 대상 탐색 통계'),
 'never label a 120-peer live result as the old one-shot 40-peer pilot');
assert.ok(uiSource.includes('model.priorityQuantRequested')&&uiSource.includes('model.priorityQuantValid'),
 'display actual source peer count from published JSON');
assert.ok(uiSource.includes('rank-caution')&&uiSource.includes('quantGlance(x.q)'),
 'make downside tier and historical stop-first metrics visible on candidate card');
console.log('v3 ranked beta: weighted legacy points, structural downside, quant date and no fake V3 promotion tests passed');
