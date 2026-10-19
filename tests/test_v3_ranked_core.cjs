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
const completeCoverage={
 schema:'v22-full-market-accounting-0.1',asof:'2026-10-16',
 complete_batch_census:true,market_listing_size:2498,
 priced_current_session_count:2369,price_failure_count:106,
 insufficient_price_history_count:23,unattempted_batch_count:0,
 flow_confirmed_count:0,pbr_observed_count:0
};
const partialCoverage={
 schema:'v22-current-listed-partial-daily-60to159-v0.1',
 asof:'2026-10-16',listed_insufficient_history_count:23,
 full_v22_score_allowed:false,confirmed_buy_signal:false,
 partial_observed_count:13
};
const quality=m.sourceCompleteness(m.prepare(snap,research,recs),
 completeCoverage,partialCoverage);
assert.equal(quality.total,2498);
assert.equal(quality.priced,2369);
assert.equal(quality.partial,13);
assert.equal(quality.flow,0);
assert.equal(quality.pbr,0);
const statusLine=m.sourceCompletenessText(quality);
assert.ok(statusLine.includes('정상 가격 분석 2,369종목 (94.8%)'));
assert.ok(statusLine.includes('부분 관측 13종목, 정식 점수 제외'));
assert.ok(statusLine.includes('자료 미확보'));
assert.equal(m.sourceCompleteness(m.prepare(snap,research,recs),
 {...completeCoverage,asof:'2026-10-15'},partialCoverage),null);
assert.equal(m.sourceCompleteness(m.prepare(snap,research,recs),
 {...completeCoverage,priced_current_session_count:2499},partialCoverage),null);
assert.equal(m.sourceCompleteness(m.prepare(snap,research,recs),
 {...completeCoverage,flow_confirmed_count:-1},partialCoverage),null);
assert.equal(m.sourceCompleteness(m.prepare(snap,research,recs),
 completeCoverage,{...partialCoverage,asof:'2026-10-15'}).partial,null);
assert.ok(m.sourceCompletenessText(null).includes('확인 대기'));
const statusPage=fs.readFileSync(path.join(__dirname,'../docs/v3-beta.html'),'utf8');
assert.ok(statusPage.includes('rankSourceQuality'));
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
const rollingBook={
 schema:'v3-dart-current-listed-annual-book-reference-0.1',asof:'2026-10-16',
 market_listed_total:2498,attempted_reference_count:1,
 observed_annual_count:1,pbr_simple_reference_count:1,
 official_exchange_pbr_verified:false,historical_point_in_time_verified:false,
 stock_buy_certified:false,score_weight_changed:false,raw_dart_account_rows_published:false,
 records:[{code:'111111',status:'observed',last_attempted_day:'2026-10-16',
   annual:{year:2025,fs_div:'OFS',receipt_id:'20260331001234',
      currency:'KRW',values:{equity:30000000000}}}],
 observations:[{code:'111111',year:2025,fs_div:'OFS',receipt_id:'20260331001234',
   market_cap_reference_day:'2026-10-16',pbr_simple_annual_reference:1.23,
   official_exchange_pbr_verified:false,financial_statement_pit_verified:false}]
};
const ready=m.observedRollingBook(rollingBook,'2026-10-16',2498);
assert.equal(ready.get('111111').value,1.23);
assert.equal(m.observedRollingBook(rollingBook,'2026-10-15',2498).size,0,
 'future source may not backfill an older stock score');
assert.equal(m.observedRollingBook(rollingBook,'2026-10-16',2497).size,0);
assert.equal(m.observedRollingBook({...rollingBook,stock_buy_certified:true},
 '2026-10-16',2498).size,0);
assert.equal(m.observedRollingBook({...rollingBook,score_weight_changed:true},
 '2026-10-16',2498).size,0);
assert.equal(m.observedRollingBook({...rollingBook,records:[rollingBook.records[0],rollingBook.records[0]],
 attempted_reference_count:2},'2026-10-16',2498).size,0);
assert.equal(m.observedRollingBook({...rollingBook,observations:[
 {...rollingBook.observations[0],pbr_simple_annual_reference:null}]},
 '2026-10-16',2498).size,0);
assert.equal(m.observedRollingBook({...rollingBook,records:[{
 ...rollingBook.records[0],annual:{...rollingBook.records[0].annual,
 receipt_id:'20271019001234'}}]},'2026-10-16',2498).size,0);
const enriched=m.prepare(snap,research,recs,official,null,rollingBook);
assert.equal(enriched.rollingBookCount,1);
assert.equal(enriched.rows.find(x=>x.r.code==='111111').rollingBook.value,1.23);
assert.equal(enriched.rows.find(x=>x.r.code==='111111').r.score,stocks[0].score);
assert.equal(enriched.rows.find(x=>x.r.code==='111111').decision.label,
 m.prepare(snap,research,recs,official).rows.find(x=>x.r.code==='111111').decision.label,
 'annual P/B evidence cannot modify recommendation verdict or v2 points');
assert.equal(m.prepare(snap,research,recs,official,null,
 {...rollingBook,asof:'2026-10-15'}).rollingBookCount,0);
const withFinance=m.prepare(snap,research,recs,official);
assert.equal(withFinance.financialReferenceCount,1);
assert.equal(withFinance.recommendationFinancialCount,1);
assert.equal(withFinance.rows[0].financialRef.year,2025);
assert.equal(withFinance.rows[0].financialRef.values.revenue,12000000000);
const scopedOfficial={...official,dart:{...official.dart,stocks:[{
 code:'111111',status:'observed',reports:[
  {...official.dart.stocks[0].reports[0],fs_div:'OFS',year:2025,
   values:{revenue:13000000000,operating_income:4100000000,
    operating_cashflow:6200000000,operating_margin_pct:31.5,
    debt_to_equity_pct:6.6}},
  {...official.dart.stocks[0].reports[0],fs_div:'OFS',year:2024,
   receipt_id:'20250320001234',
   values:{revenue:12000000000,operating_income:3400000000,
    operating_cashflow:3000000000,operating_margin_pct:28.3,debt_to_equity_pct:6.1}},
  {...official.dart.stocks[0].reports[0],fs_div:'OFS',year:2023,
   receipt_id:'20240320001234',
   values:{revenue:14000000000,operating_income:4300000000,
    operating_cashflow:3600000000,operating_margin_pct:30.7,debt_to_equity_pct:9.1}},
  {...official.dart.stocks[0].reports[0],fs_div:'CFS',year:2024,
   receipt_id:'20250320001234',
   values:{revenue:999999999999}},
  {...official.dart.stocks[0].reports[0],fs_div:'OFS',year:2026,
   receipt_id:'20271111001234',values:{revenue:888888888888}},
 ]}]}};
const historical=m.observedAnnuals(scopedOfficial,'2026-10-16').get('111111');
assert.deepEqual(historical.history.map(x=>x.year),[2025,2024,2023]);
assert.ok(historical.history.every(x=>x.fs_div==='OFS'),
 'never mix different statement scopes in a growth trend');
assert.ok(m.financialHistoryTable(historical).includes('2023년'));
assert.ok(m.financialHistoryTable(historical).includes('130억원'));
assert.ok(!m.financialHistoryTable(historical).includes('9,999억원'));
assert.ok(!m.financialHistoryTable(historical).includes('2026년'));
assert.equal(m.observedAnnuals({...scopedOfficial,asof:'2026-10-15'},'2026-10-16').size,0,
 'a future or prior matching annual feed must not be used without asof consistency');
const spoofReceipt=structuredClone(scopedOfficial);
spoofReceipt.dart.stocks[0].reports=spoofReceipt.dart.stocks[0].reports.map(t=>(
 {...t,receipt_id:'20271019001234'}));
assert.equal(m.observedAnnuals(spoofReceipt,'2026-10-16').size,0,
 'later-published accounts cannot leak into an earlier stock signal date');
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:500},{fs_div:'OFS',values:{equity:50_000_000_000}}),1,
 'observed latest OFS year-end total book equity can form a plainly unverified simple P/B reference');
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:1000},{fs_div:'OFS',values:{equity:50_000_000_000}}),2);
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:500},{fs_div:'CFS',values:{equity:50_000_000_000}}),null,
 'consolidated equity includes noncontrolling stakes so no simplified official PBR can be inferred');
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:null},{fs_div:'OFS',values:{equity:50_000_000_000}}),null);
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:500},{fs_div:'OFS',values:{equity:null}}),null);
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:500},{fs_div:'OFS',values:{equity:-1}}),null);
const annualCardUI=fs.readFileSync(path.join(__dirname,'../docs/v3-ranking.js'),'utf8');
assert.ok(annualCardUI.includes('rank-finance-reference'));
assert.ok(annualCardUI.includes('bookReference.toFixed(2)'));
assert.ok(annualCardUI.includes('정식 PBR 점수 제외'));
assert.ok(annualCardUI.includes('비공식 간이 P/B 미산출'));
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:336.96},{fs_div:'OFS',values:{equity:49_629_200_916}}),0.68,
 'verified-formula rounded annual equity reference; no exchange-reported PBR claim');
assert.equal(m.simpleAnnualOfsBookReference(
 {market_cap_eok:1517.12},{fs_div:'OFS',values:{equity:152_012_858_334}}),1.0,
 'separately observed OFS book-value reference is not an investment score');
const plainScore=m.prepare(snap,research,recs);
const bookReferenceScore=m.prepare(snap,research,recs,scopedOfficial);
assert.equal(bookReferenceScore.rows[0].r.score,plainScore.rows[0].r.score,
 'historical annual P/B reference cannot silently revise v2 score');
assert.equal(bookReferenceScore.rows[0].decision.label,plainScore.rows[0].decision.label,
 'a low annual book reference cannot independently cause a buy decision');
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
 asof:'2026-10-16',status:'exploratory',peer_cohort_requested:40,peer_cohort_valid:29,
 raw_ohlcv_copied:false,verified_predictive_accuracy:false,
 stocks:[{code:'111111',status:'exploratory',reference_price_date:'2026-10-16',
         peer_cohort_requested:40,periods:{
          '스윙 1개월':research.stocks[0].periods['스윙 1개월']}}]};
const oldBroad={status:'exploratory',asof:'2026-10-15',stocks:[]};
const targeted=m.prepare(snap,oldBroad,recs,undefined,priorityFeed);
assert.equal(targeted.recommendationQuantCount,1);
assert.equal(targeted.recommendationUsableQuantCount,1);
assert.equal(targeted.rows[0].quantSource,'추천 우선 연구 40종목 요청 / 29종목 실제 이력 확보');
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
// Shortlist is not the only usable research entry point: a separate, strictly
// risk-gated technical watchlist is derived from actual v2.2 observed fields.
const watchBase=structuredClone(stocks[0]);
watchBase.obv_rising=true;
watchBase.divergence=true;
watchBase.price20=2;
watchBase.price60=5;
watchBase.one_day_volume_ratio=1.5;
const watchModel=m.prepare({...snap,stocks:[watchBase,stocks[1],stocks[2]]},research,recs);
assert.equal(watchModel.watchCount,1);
assert.equal(watchModel.obvLagCount,1);
assert.deepEqual(m.select(watchModel,'watch').map(x=>x.r.code),['111111']);
assert.deepEqual(m.select(watchModel,'pattern').map(x=>x.r.code),['111111']);
assert.equal(m.select(watchModel,'rsi').length,0);
assert.deepEqual(m.select(watchModel,'obv').map(x=>x.r.code),['111111']);
assert.equal(watchModel.recommendationCount,1,'watch-only does not manufacture shortlist');
assert.equal(watchModel.rows[0].decision.label,'조건부 모의 검토',
 'a technical watch filter never promotes an unverified buy signal');
assert.ok(m.watchEvidence(watchBase).labels.some(s=>s.includes('OBV·가격 미반영 보조')),
 'OBV is supporting evidence, not the watchlist heading');
assert.ok(m.watchEvidence({...watchBase,obv_rising:false})?.pattern,
 'preferred chart pattern must not require OBV rising');
assert.equal(m.watchEvidence({...watchBase,obv_rising:false})?.obvLag,false,
 'missing OBV must not be mislabeled accumulation');
assert.equal(m.watchEvidence({...watchBase,coverage_pct:30}),null);
assert.equal(m.watchEvidence({...watchBase,stop_line:null}),null);
assert.equal(m.watchEvidence({...watchBase,price60:48}),null,
 'exclude already rallied, higher chase-risk shares');
assert.equal(m.watchEvidence({...watchBase,risk_flags:['거래대금 부족']}),null,
 'technical momentum does not override explicit liquidity danger');
assert.equal(m.watchEvidence({...watchBase,divergence:false,
 patterns:['삼각수렴 후보'],price60:14})?.obvLag,false,
 'pattern candidates remain observations distinct from OBV-price lag');
assert.equal(m.watchEvidence({...watchBase,divergence:false,
 patterns:[],phase:'중립/기타'}),null,
 'no structure or lag implies no watchlist entry');
const flagNoObv={...watchBase,obv_rising:false,divergence:false,
 price_date:'2026-10-16',patterns:[],phase:'중립/기타',pattern_research:{
 schema:'v3-pattern-first-daily-research-0.1',asof:'2026-10-16',
 status:'heuristic_unverified',
 timeframe:'daily',official_regular_session_verified:false,
 scoring_allowed:false,buy_signal:false,rsi14:43,
 rsi30_cross_recent:false,rsi_bullish_divergence_recent:false,
 patterns:[{type:'bull_flag',stage:'forming',reference_breakout:114,
            reference_flag_low:103}]}};
assert.equal(m.watchEvidence(flagNoObv)?.pattern,true,
 'Bull Flag must be detected even without supportive OBV');
assert.ok(m.watchEvidence(flagNoObv).labels.join(' ').includes('Bull Flag'));
const db={...flagNoObv,pattern_research:{...flagNoObv.pattern_research,
 patterns:[{type:'double_bottom',stage:'breakout_observed'}]}};
assert.ok(m.watchEvidence(db).labels.join(' ').includes('더블바텀'));
const rsiRecovery={...flagNoObv,pattern_research:{
 ...flagNoObv.pattern_research,patterns:[],rsi14:37,
 rsi30_cross_recent:true}};
assert.equal(m.watchEvidence(rsiRecovery).rsiSignal,true,
 'RSI 30 re-cross is a standalone observation without OBV');
const divRecovery={...rsiRecovery,pattern_research:{
 ...rsiRecovery.pattern_research,rsi30_cross_recent:false,
 rsi_bullish_divergence_recent:true}};
assert.ok(m.watchEvidence(divRecovery).labels.join(' ').includes('상승다이버전스'));
assert.equal(m.watchEvidence({...flagNoObv,pattern_research:{
 ...flagNoObv.pattern_research,buy_signal:true}}),null,
 'invented promotion of a pattern to certified buy must be rejected');
assert.equal(m.watchEvidence({...flagNoObv,pattern_research:{
 ...flagNoObv.pattern_research,patterns:[{type:'dragon',stage:'forming'}]}}),null,
 'unknown pattern type cannot silently mint an observed favorite');
assert.equal(m.watchEvidence({...flagNoObv,risk_flags:['거래대금 부족']}),null,
 'Bull Flag must not bypass the liquidity risk exclusion');
assert.equal(m.watchEvidence({...flagNoObv,stop_line:null}),null);
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
assert.equal(m.stockPage('005930'),'https://finance.naver.com/item/main.naver?code=005930');
assert.equal(m.stockPage('005930?x=1'),null,'do not allow user-supplied external link injection');
assert.equal(m.stockPage('<script>'),null);
const home=fs.readFileSync(path.join(__dirname,'../docs/v3-beta.html'),'utf8');
assert.ok(home.includes('차트 패턴 중심 종목 발굴 · 퀀트 검증'));
assert.ok(home.includes('폴링 웻지')&&home.includes('역헤드앤숄더')&&home.includes('더블바텀')&&home.includes('Bull Flag'));
assert.ok(home.includes('v3-ranking.js')&&home.includes('v3-ranking.css'));
assert.ok(home.includes('심층 선별 후보')&&home.includes('퀀트 자료 있는 종목'));
assert.ok(home.includes('data-mode="watch" aria-pressed="true"')&&
 home.includes('data-mode="pattern"')&&home.includes('data-mode="rsi"')&&
 home.includes('data-mode="obv"')&&home.includes('Bull Flag')&&
 home.includes('매수 미인증'),
 'user-facing first view must expose observable technical research without buy claim');
assert.ok(home.includes('v3-manual.html'));
assert.ok(home.includes('rankCandidateQuant'));
assert.ok(!home.includes('수익률 100% 보장'));
const uiSource=fs.readFileSync(path.join(__dirname,'../docs/v3-ranking.js'),'utf8');
assert.ok(!uiSource.includes('40종목 대상 탐색 통계'),
 'never label a 120-peer live result as the old one-shot 40-peer pilot');
assert.ok(uiSource.includes('model.priorityQuantRequested')&&uiSource.includes('model.priorityQuantValid'),
 'display actual source peer count from published JSON');
assert.ok(uiSource.includes('v3-dart-book-reference.json')&&
 uiSource.includes('model.rollingBookCount'),
 'rolling multi-company annual accounts should appear on ranked user view without scoring');
assert.ok(uiSource.includes('rel="noopener noreferrer"')&&uiSource.includes('종목 차트'),
 'validated per-ticker chart navigation should work without new quote scraping');
assert.ok(uiSource.includes('rank-caution')&&uiSource.includes('quantGlance(x.q)'),
 'make downside tier and historical stop-first metrics visible on candidate card');
// Also exercise the *published* actual market source. Do not assert a minimum
// count: legitimate weak-market periods may yield zero safe watch candidates.
const liveDaily=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
const liveWatch=m.prepare(liveDaily,null,null);
assert.ok(liveWatch.watchCount<=liveWatch.rows.length);
assert.ok(liveWatch.obvLagCount<=liveWatch.rows.length);
assert.ok(liveWatch.patternWatchCount<=liveWatch.watchCount);
assert.ok(liveWatch.rsiWatchCount<=liveWatch.watchCount);
assert.ok(m.select(liveWatch,'watch').every(x=>x.watch&&!x.highRisk&&
 x.risk>=3&&x.risk<=15));
assert.ok(m.select(liveWatch,'pattern').every(x=>x.watch?.pattern));
assert.ok(m.select(liveWatch,'rsi').every(x=>x.watch?.rsiSignal));
console.log('Published actual market technical watch evidence:',liveDaily.date,
  'scored=',liveWatch.rows.length,'watch=',liveWatch.watchCount,
  'patterns=',liveWatch.patternWatchCount,
  'RSI=',liveWatch.rsiWatchCount,'OBV-support=',liveWatch.obvLagCount);
console.log('v3 ranked beta: weighted legacy points, structural downside, quant date and no fake V3 promotion tests passed');
