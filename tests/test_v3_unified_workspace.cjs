'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const unified=require('../docs/workspace.js');
const legacy=require('../docs/v3-ranking.js');
const html=fs.readFileSync(path.join(__dirname,'../docs/workspace.html'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../docs/workspace.css'),'utf8');
const entry=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const backup=fs.readFileSync(path.join(__dirname,'../docs/legacy.html'),'utf8');
const source=fs.readFileSync(path.join(__dirname,'../docs/workspace.js'),'utf8');
const ranking=fs.readFileSync(path.join(__dirname,'../docs/v3-ranking.js'),'utf8');
// A weekly group is evidence replay, NOT full verification or a fresh API response.
assert.equal(unified.weeklyCoverageText(null,'2026-10-20'),'');
const weekly={schema:'v3-seven-day-research-coverage-0.1',
 market_asof:'2026-10-19',run_kst:'2026-10-20',cycle_start:'2026-10-20',
 listed_total:2497,processed_this_cycle:357,unprocessed_this_cycle:2140,
 cohort_counts:[357,357,357,357,357,356,356],
 source_collection_calls:0,verified_full_six_pillar_issuer_count:0,
 source_reference_counts_this_cycle:{technical_parts:321}};
assert.ok(unified.weeklyCoverageText(weekly,'2026-10-20')
 .includes('357/2497종목'));
assert.ok(unified.weeklyCoverageText(weekly,'2026-10-20')
 .includes('기존 원본 재계산'));
assert.equal(unified.weeklyCoverageText({...weekly,source_collection_calls:6},
 '2026-10-20'),'');
assert.equal(unified.weeklyCoverageText({...weekly,processed_this_cycle:400},
 '2026-10-20'),'');
// Genuine FCF source receipts show counts only, never a synthetic 15pt rank.
const rawFCF={schema:'v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1',
 asof:'2026-10-20',listed_total:2497,
 source_same_filing_scope_receipt_checked:true,
 hist_PIT_verified:false,four_year_financial15_complete:false,
 valuation15_complete:false,joined_to_main_v3_ranking:false,
 buy_recommendations_authorized:false,six_pillar_confirmed_100pt:null,
 minimum_same_market_peers:30,financial_fcf_weight_available_max:4,
 valuation_fcf_weight_available_max:5,
 real_fcf_assets_three_year_raw_issuer_count:24,
 real_latest_fcf_yield_raw_issuer_count:33,
 financial_fcf4_research_candidate_issuer_count:0,
 valuation_fcf5_research_candidate_issuer_count:0,rows:[]};
assert.ok(unified.actualCashflowCoverageText(rawFCF,'2026-10-20',2497)
 .includes('실제 FCF 3년 자료 24종목'));
assert.ok(unified.actualCashflowCoverageText(rawFCF,'2026-10-20',2497)
 .includes('4점 잠정계산 0종목'));
assert.equal(unified.actualCashflowCoverageText(rawFCF,'2026-10-21',2497),'');
assert.equal(unified.actualCashflowCoverageText(rawFCF,'2026-10-20',2496),'');
assert.equal(unified.actualCashflowCoverageText(
 {...rawFCF,buy_recommendations_authorized:true},'2026-10-20',2497),'');
assert.equal(unified.actualCashflowCoverageText(
 {...rawFCF,financial_fcf4_research_candidate_issuer_count:25},
 '2026-10-20',2497),'');
assert.equal(unified.actualCashflowCoverageText(
 {...rawFCF,hist_PIT_verified:true},'2026-10-20',2497),'');
assert.ok(source.includes("'v3-dart-fcf-research-preview.json'")&&
 source.includes('actualCashflowCoverageText(actualCashflowCoverage,model.date,model.marketEligible)'));
// Four-year DART source is read by primary V3; reference CAGR/margin
// remains informational until revisions/PIT and full 15+15 scoring pass.
const fourRecords=[2022,2023,2024,2025].map(yr=>({
 year:yr,fs_div:'CFS',currency:'KRW',
 receipt_id:String(yr+1)+'0318001162',values:{revenue:1000+yr}
}));
const annualFour={schema:'v3-four-year-dart-financial-evidence-0.1',
 asof:'2026-10-19',listed_total:2497,pilot_issuer_count:1,
 four_years_all_financial_pillars_certified:false,full_v3_ready:false,
 raw_account_rows_stored:false,confirmed_six_pillar_ranks:null,
 records:[{code:'005930',v3_full100_investable_score:null,
 financial15_confirmed_points:null,valuation15_confirmed_points:null,
 four_year_financial_pit_authenticated:false,
 historical_revisions_authenticated:false,buy_certified:false,
 same_scope_four_years:true,accounting_scope:'CFS',
 reports:fourRecords,annual_revenue_cagr3_reference_pct:12.3,
 three_year_operating_margin_mean_reference_pct:9.2,
 fcf_assets_reference_pct:null,net_debt_equity_reference_pct:null,
 valuation_official_pbr:null}]};
const dated=legacy.observedFourYearEvidence(annualFour,'2026-10-20',2497);
assert.equal(dated.get('005930').cagr,12.3);
assert.equal(dated.get('005930').margin,9.2);
assert.equal(dated.get('005930').authenticated,false);
assert.equal(legacy.observedFourYearEvidence(
 {...annualFour,asof:'2026-10-21'},'2026-10-20',2497).size,0,
 'future source date cannot be used');
assert.equal(legacy.observedFourYearEvidence(
 annualFour,'2026-11-25',2497).size,0,'stale fiscal source must expire');
assert.ok(source.includes('v3-dart-four-year-reference.json')&&
 source.includes('core.prepare(...responses.slice(0,6),responses[8])'),
 'primary workspace must ingest separate four-year DART evidence');
assert.ok(source.includes('4개년 연간공시 연구 참고')&&
 source.includes('공시 정정/PIT 검증 전으로 재무 15점 및 밸류 15점은 미산출'),
 'four-year metrics cannot silently become a certified 30 point ranking');
assert.equal(Object.values(unified.WEIGHTS).reduce((a,b)=>a+b,0),100);
assert.deepEqual(unified.WEIGHTS,{ias:20,technical:25,financial:15,valuation:15,industry:15,momentum:10});
assert.equal(unified.PILLARS[0][0],'ias');
assert.equal(unified.PILLARS[0][1],'IAS·수급',
 'IAS must remain visibly identified as a distinct /20 pillar on every ticker');
assert.equal(unified.IAS_COMPONENTS.reduce((total,entry)=>total+entry[1],0),100,
 'six internal IAS evidence weights must still sum to IAS 100, scaled to V3 20');
assert.deepEqual(unified.IAS_COMPONENTS.map(entry=>entry[1]),[20,25,15,10,15,15]);
assert.ok(unified.iasParts().includes('IAS 내부 100점'));
assert.ok(unified.iasParts().includes('20·60·120·250일 누적 순매수'));
assert.ok(unified.iasParts().includes('미검증'));
assert.ok(source.includes('data-pillar="ias"')&&
 source.includes('IAS 원장 미검증')&&
 source.includes('IAS(수급) /20'),
 'IAS missing numeric value must display as identifiable 20pt unverified investor ledger, not disappear');
assert.ok(css.includes('.u-scoresix span[data-pillar="ias"] small'),
 'mobile IAS identity must remain legible');
assert.equal(unified.validDate('2026-02-30'),false,'calendar rollover is invalid');
assert.deepEqual(unified.marketFreshness('2026-10-20',new Date('2026-10-19T15:10:00Z')),
 {status:'current',calendarDays:0},'KST midnight boundary');
assert.deepEqual(unified.marketFreshness('2026-10-19',new Date('2026-10-19T15:10:00Z')),
 {status:'current',calendarDays:1});
assert.deepEqual(unified.marketFreshness('2026-10-19',new Date('2026-10-26T00:00:00Z')),
 {status:'stale',calendarDays:7});
assert.deepEqual(unified.marketFreshness('2026-10-21',new Date('2026-10-20T00:00:00Z')),
 {status:'future',calendarDays:-1});
assert.ok(html.includes('id="unifiedApp"'));
assert.ok(html.includes('<details class="u-intro" id="marketSummary">'),
 'market observation details must be collapsed on initial mobile load');
assert.ok(!html.includes('<details class="u-intro" id="marketSummary" open'),
 'default market observation must be closed');
assert.ok(html.includes('<details class="u-extra-filters" id="advancedFilters">'),
 'oversized search filter area must be optional, not fixed');
assert.ok(html.indexOf('id="stockSearch"')<html.indexOf('id="tabs"'),
 'global ticker search must be above secondary chart and RSI tabs');
assert.ok(source.includes("if(search)return"),'search must bypass TOP100 and tab scopes');
assert.ok(source.includes("미검증"),'avoid showing dash-only factors without explanation');
assert.ok(source.includes("+' /'+i.max"),'each domain must show its approved maximum');
assert.ok(html.includes('id="top100Only"'),'one-tap top 100 filter required');
assert.ok(html.includes('<option value="score" selected>V3 잠정점수순</option>'),
 'default selection must explicitly use V3 provisional observations');
assert.ok(source.includes("sort='score'"),'default rank must use displayed score');
assert.ok(css.includes('color-scheme:dark')&&css.includes('.u-scoresix'),
 'approved dark-green theme and 6 V3 pillar boxes must remain');
assert.ok(css.includes('.u-scoresix span.u-total-factor')&&
 css.includes('box-shadow:inset 0 0 0 1.5px #e66a72'),
 'each unverified factor card should have a red inner outline');
assert.ok(source.includes('x.composite.pillars.map')&&
 source.includes("i.candidate===null?'—':i.candidate.toFixed(1)"),
 'calculated provisional research candidates remain visible');
assert.ok(source.includes('researchComposite(r)')&&
 source.includes('산출 가능 배점'),
 'V3 score coverage must accompany any available research sum');
assert.ok(source.includes('잠정 합계')&&source.includes('미검증'),
 'do not treat research aggregate as a verified investment decision');
assert.equal((html.match(/id="stockSearch"/g)||[]).length,1);
for(const s of ['id="stockList"','id="detail"','id="market"','id="sort"',
 'id="tabs"','id="saferOnly"','id="reload"','id="statV3"',
 '시장 · 정렬 · 손절 위험 상세 설정','V3 잠정점수 산출 종목','차트 패턴'])
 assert.ok(html.includes(s),'single screen missing '+s);
assert.ok(html.includes('workspace.js')&&html.includes('v3-ranking.js'));
assert.ok(entry.includes('id="unifiedApp"')&&entry.includes('workspace.js'),
 'homepage itself must serve unified V3 without a redirect');
assert.ok(!entry.includes('http-equiv="refresh"'),'avoid legacy intermediate page or double navigation');
assert.ok(backup.includes('통합 스크리너로 돌아가기'),'archived previous UI must be available');
assert.ok(!backup.includes('http-equiv="refresh"'),'old research UI should not auto-redirect');
assert.ok(ranking.includes("if(root.document.getElementById('rankStatus'))init();"),
 'legacy beta mounting cannot crash the new unified main app');
assert.ok(css.includes('@media(max-width:860px)')&&css.includes('@media(max-width:480px)'),
 'mobile stacked research UI is required');
assert.ok(html.includes("script-src 'self'")&&html.includes("connect-src 'self'"),
 'CSP must disallow untrusted JS/data imports');
assert.ok(source.includes('six_pillar_full_candidate_only')===false,
 'never build a full v3 investment ranking in a front-end shortcut');
assert.ok(!source.includes('localStorage')&&!source.includes('github_token'),
 'UI must not require credentials or persistent private trade settings');

const shape={
 schema:'v3-pattern-first-daily-research-0.1',status:'heuristic_unverified',
 timeframe:'daily',asof:'2026-10-20',completed_week_only:undefined,
 rsi14:44,rsi30_cross_recent:true,rsi_bullish_divergence_recent:true,
 patterns:[{type:'bull_flag',stage:'forming'}],
 pattern_geometry:{status:'heuristic_unvalidated',
  falling_wedge_candidate:false,inverse_head_shoulders_breakout_candidate:false,
  triangle_candidate:false,post_rise_consolidation_candidate:false},
 scoring_allowed:false,buy_signal:false,official_regular_session_verified:false
};
const row={
 code:'005930',name:'샘플회사',market:'KOSPI',
 price:100,stop_line:91,stop_pct:-9,score:70,coverage_pct:70,
 price20:1,price60:-7,risk_flags:[],price_date:'2026-10-20',
 patterns:[],obv_rising:false,divergence:false,phase:'조정 중',
 pattern_research:shape,
 pattern_weekly_research:{...shape,timeframe:'weekly',asof:'2026-10-16',
  completed_week_only:true,rsi30_cross_recent:false,rsi_bullish_divergence_recent:false,
  patterns:[{type:'double_bottom',stage:'breakout_observed'}]},
 v3_pattern_integrated:{schema:'v3-pattern-within-six-pillars-research-0.4',
  asof:'2026-10-20',technical_maximum:25,
  technical_research_candidate:22.8,investable_score:null,
  buy_certified:false,v3_release_ready:false}
};
assert.ok(unified.validPattern(row,'daily'));
assert.ok(unified.validPattern(row,'weekly'));
assert.ok(unified.evidence(row,'daily').some(x=>x.label==='Bull Flag'));
assert.ok(unified.evidence(row,'weekly').some(x=>x.label==='더블바텀'));
assert.ok(unified.evidence(row,'daily').some(x=>x.label==='RSI 30 재돌파'));
assert.ok(unified.evidence(row,'daily').some(x=>x.label==='RSI 상승다이버전스'));
assert.equal(unified.technicalCandidate(row),22.8);
assert.deepEqual(unified.pillarState(row).map(x=>x.candidate),
 [null,22.8,null,null,null,null]);
const missingIAS=unified.pillarState(row)[0];
assert.equal(missingIAS.label,'IAS·수급');
assert.equal(missingIAS.max,20);
assert.equal(missingIAS.candidate,null);
assert.equal(missingIAS.available,0);
assert.ok(unified.iasStatus(missingIAS).includes('결측은 0점이 아닙니다'));
assert.equal(unified.researchComposite(row).pillars.length,6,
 'unverified IAS still occupies one of six visible domain cells');
const observedResearchIAS={...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,
 six_pillar_maximum:unified.WEIGHTS,
 six_pillar_candidates:{ias:7.5,technical:null,financial:null,
                        valuation:null,industry:null,momentum:null}
}};
assert.equal(unified.pillarState(observedResearchIAS)[0].candidate,7.5,
 'a separately supplied same-date bounded provisional IAS may be shown as such');
assert.equal(unified.researchComposite(observedResearchIAS).available,45,
 'IAS candidate /20 must contribute 20 available, not old 25 or fabricated amount');
assert.ok(unified.iasStatus(unified.pillarState(observedResearchIAS)[0])
 .includes('연구용 잠정값'));
assert.equal(unified.pillarState({...row,score:100})[0].candidate,null,
 'old v2.2 overall score must never be promoted into IAS');

assert.ok(!unified.riskState({r:row},legacy).unsafe);
assert.equal(unified.riskState({r:{...row,stop_line:60,stop_pct:-40}},legacy).unsafe,true);
assert.equal(unified.technicalCandidate({...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,investable_score:70}}),null,
 'forged completed trade score invalidates display');
assert.equal(unified.technicalCandidate({...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,technical_research_candidate:26}}),null);
for(const item of [
 {...row,pattern_research:{...shape,asof:'2026-10-21'}},
 {...row,pattern_research:{...shape,buy_signal:true}},
 {...row,pattern_research:{...shape,patterns:[{type:'invented',stage:'forming'}]}},
 {...row,pattern_research:{...shape,scoring_allowed:true}},
])assert.equal(unified.validPattern(item,'daily'),null,'invalid daily source must fail closed');
for(const item of [
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,completed_week_only:false}},
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,asof:'2026-10-23'}},
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,asof:'2026-10-17'}},
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,asof:'2026-10-02'}}
])assert.equal(unified.validPattern(item,'weekly'),null,'incomplete/stale/future week must fail closed');
assert.equal(unified.esc('<script>"/&'), '&lt;script&gt;&quot;/&amp;');

// Only calculable same-date research candidates can be shown, even if
// the source has not yet certified them for trading. Never insert missing=0.
const partial={...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,
 technical_research_candidate:null,
 technical_observed_maximum:11,
 technical_parts:{
  daily_pattern:3.5,weekly_pattern:null,daily_rsi:1,weekly_rsi:null,
  weekly_ma120:null,volume_confirmation:2
 }
}};
assert.equal(unified.technicalCandidate(partial),null,
 'incomplete technical factor cannot masquerade as fully scored 25-point factor');
assert.deepEqual(unified.technicalEvidence(partial),
 {earned:6.5,available:11,fullyObserved:false});
assert.equal(unified.researchComposite(partial).earned,6.5);
assert.equal(unified.researchComposite(partial).available,11);
assert.equal(unified.researchComposite(partial).missing,89);
assert.deepEqual(unified.pillarState(partial).map(p=>p.candidate),
 [null,6.5,null,null,null,null]);
// Backfilled V3 values from the bounded 7-day rotation require exact date,
// same ticker, source counts, numeric parts and explicit noncertification.
const missingAdapter={...partial,v3_pattern_integrated:null};
const backedRec={
 code:missingAdapter.code,market_source_asof:'2026-10-20',
 last_recalculated_kst:'2026-10-20',available_weight:11,
 research_score_points:6.5,technical_rebuilt_from_saved_source:true,
 technical_research_backfill:partial.v3_pattern_integrated,
 six_pillar_research_score_confirmed:false
};
const backState={schema:'v3-seven-day-research-source-rotation-0.1',
 market_asof:'2026-10-20',cycle_start:'2026-10-20',run_kst:'2026-10-20',
 source_collection_calls:0,legacy_v22_score_used:false,
 v3_live_full_six_pillar_score_claimed:false,
 records:{'005930':backedRec}};
const backModel={date:'2026-10-20',rows:[{r:missingAdapter}]};
assert.equal(unified.applyRotationTechnicalBackfill(backModel,backState),1);
assert.equal(unified.researchComposite(missingAdapter).earned,6.5);
assert.equal(unified.researchComposite(missingAdapter).available,11);
assert.equal(unified.applyRotationTechnicalBackfill(backModel,backState),0,
 'proven adapter cannot be overwritten on subsequent replay');
for(const bad of [
 {...backState,market_asof:'2026-10-19'},
 {...backState,source_collection_calls:4},
 {...backState,records:{'005930':{...backedRec,research_score_points:100}}},
 {...backState,records:{'005930':{...backedRec,technical_research_backfill:{
  ...partial.v3_pattern_integrated,investable_score:99}}}},
]){
 const shadow={date:'2026-10-20',rows:[{r:{...missingAdapter,v3_pattern_integrated:null}}]};
 assert.equal(unified.applyRotationTechnicalBackfill(shadow,bad),0,
  'source/date/score mismatches must never turn into V3 user scores');
}
const momentumReference={
 schema:'v3-observed-market-momentum-8of10-research-0.1',
 asof:'2026-10-20',market:'KOSPI',momentum_maximum:10,
 momentum_parts:{relative20:3,relative60:2,positive_days20:null},
 momentum_observed_maximum:8,momentum_research_candidate:5,
 peer20_count:100,peer60_count:100,
 buy_certified:false,investable_score:null,
 market_benchmark_date_independently_verified:false,
 source_unadjusted_corporate_events_verified:false,
 positive_days20_observed:false
};
const partialMomentum={...row,v3_pattern_integrated:null};
const stateWithMomentum={...backState,records:{
 '005930':{...backedRec,technical_rebuilt_from_saved_source:false,
 technical_research_backfill:null,momentum_research_reference:momentumReference,
 momentum_research_points:5,momentum_available_weight:8}
}};
assert.equal(unified.applyRotationTechnicalBackfill(
 {date:'2026-10-20',rows:[{r:partialMomentum}]},stateWithMomentum),1);
assert.equal(unified.researchComposite(partialMomentum).earned,5);
assert.equal(unified.researchComposite(partialMomentum).available,8);
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,
 market_benchmark_date_independently_verified:true}}),null);
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,peer20_count:2}}),null);
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,
 momentum_parts:{...momentumReference.momentum_parts,positive_days20:2}}}),null);
const missing={...row,v3_pattern_integrated:null};
assert.equal(unified.researchComposite(missing).earned,null,
 'missing factor must not appear as 0.0/100');
assert.equal(unified.researchComposite(missing).available,0);
const zero={...partial,v3_pattern_integrated:{
 ...partial.v3_pattern_integrated,
 technical_parts:{daily_pattern:0,weekly_pattern:null,daily_rsi:0,
  weekly_rsi:null,weekly_ma120:null,volume_confirmation:0}
}};
assert.equal(unified.researchComposite(zero).earned,0,
 'legitimate calculated zero stays a numeric zero');
assert.equal(unified.researchComposite(zero).available,11);
const timeMismatch={...partial,v3_pattern_integrated:{
 ...partial.v3_pattern_integrated,asof:'2026-10-19'}};
assert.equal(unified.researchComposite(timeMismatch).earned,null,
 'mismatched source date blocks numeric aggregate');
const partial3={...partial,v3_pattern_integrated:{
 ...partial.v3_pattern_integrated,
 six_pillar_maximum:unified.WEIGHTS,
 six_pillar_candidates:{ias:null,technical:null,financial:6,
   valuation:null,industry:null,momentum:4}
}};
assert.equal(unified.researchComposite(partial3).earned,12.5);
assert.equal(unified.researchComposite(partial3).available,26,
 'previously supplied momentum data cannot enter a V3 subtotal without independently checked 8-point benchmark research');
assert.equal(unified.researchComposite({...partial3,
 v3_pattern_integrated:{...partial3.v3_pattern_integrated,
 six_pillar_maximum:{...unified.WEIGHTS,financial:25}}
}).earned,6.5,'out-of-contract candidate maxima cannot inflate a total');

const highLegacy={...row,code:'000001',name:'높은 구형점수',
 score:98,v3_pattern_integrated:null,
 pattern_research:null,pattern_weekly_research:null};
const safe={r:row,q:null,watch:null,quant:false,researchCandidate:false};
const unsafe={r:highLegacy,q:null,watch:null,quant:false,researchCandidate:false};
const model={date:'2026-10-20',rows:[unsafe,safe],marketEligible:2,
 rollingBookCount:0,quantCount:0};
assert.deepEqual(unified.rankItems(model,legacy,'all').map(x=>x.r.code),
 ['005930','000001'],'V2.2 98 points cannot outrank V3 research evidence');
assert.equal(unified.rankItems(model,legacy,'all')[0].composite.earned,22.8);
assert.equal(unified.rankItems(model,legacy,'all')[1].composite.earned,null);
assert.equal(unified.rankItems(model,legacy,'patterns').length,1);
assert.equal(unified.rankItems(model,legacy,'rsi').length,1);
assert.equal(unified.rankItems(model,legacy,'quant').length,0);
assert.deepEqual(unified.rankItems(model,legacy,'all','all','높은').map(x=>x.r.code),
 ['000001']);
assert.equal(unified.summary(model,legacy).officialFullScore,0,
 'provisional sum is not a final investment ranking');
assert.equal(unified.summary(model,legacy).provisional,1);
const safeHighRisk={...row,code:'111111',name:'손절위험',stop_line:60,
 stop_pct:-40,v3_pattern_integrated:{...row.v3_pattern_integrated,
 technical_research_candidate:24}};
const risks={...model,rows:[safe,{...safe,r:safeHighRisk}]};
assert.equal(unified.rankItems(risks,legacy,'all')[0].r.code,'111111',
 'risk warnings should not silently mutate the visible observed-score ordering');
assert.equal(unified.rankItems(risks,legacy,'all','all','','score',true).length,1);
assert.ok(unified.rankItems(risks,legacy,'all')[0].risk.unsafe);

// A 100-candidate browse filter operates ONLY on stocks with research values.
const candidates=Array.from({length:106},(_,i)=>({
 ...safe,r:{...row,code:String(i).padStart(6,'0'),name:'모의'+i,
  score:i, // old 2.2 points deliberately run in opposite direction
  v3_pattern_integrated:{...row.v3_pattern_integrated,
   technical_research_candidate:Math.round((25-i*0.1)*10)/10}}
}));
const many={...model,rows:candidates.reverse()};
const top=unified.rankItems(many,legacy,'all','all','','score',false,true);
assert.equal(top.length,100);
assert.equal(top[0].r.code,'000000');
assert.equal(top[99].r.code,'000099');
assert.equal(top[0].candidatePosition,1);
assert.equal(top[99].candidatePosition,100);
const outsideTop=many.rows[0].r;
const globalSearch=unified.rankItems(many,legacy,'rsi','KOSDAQ',
 outsideTop.code,'score',true,true);
assert.deepEqual(globalSearch.map(x=>x.r.code),[outsideTop.code],
 'search always bypasses the optional top100 and tab filter');
assert.equal(globalSearch[0].candidatePosition,106);
const noEvidence={...model,rows:[unsafe]};
assert.equal(unified.rankItems(noEvidence,legacy,'all','all','','score',false,true).length,0,
 'TOP100 does not fabricate observed stock candidates');
assert.equal(unified.rankItems(noEvidence,legacy,'all').length,1,
 'the full list retains unavailable-source tickers for direct inspection');

// Historical OpenDART BS/IS partial 8+5 points integrate into six-area UI
// only when same-market date, separately verified research provenance matches.
const funded={...row,market:'KOSPI'};
const display={date:'2026-10-20',rows:[{r:funded}]};
const bulkParts={
 schema:'v3-dart-multi-year-research-parts-0.1',
 asof:'2026-10-20',listed_total:2497,model_financial_full_points:15,
 model_valuation_full_points:15,financial_partial_max_possible:8,
 valuation_partial_max_possible:10,
 historical_PIT_certified:false,fully_verified_six_pillar_count:0,
 all_scores_provisional_unverified:true,
 rows:[{code:funded.code,market:'KOSPI',asof:'2026-10-20',
   confirmed_v3_score:null,scoring_certified:false,
   financial_candidate_points:6,financial_available_max:8,
   valuation_candidate_points:3.5,valuation_available_max:5,
   combined_provisional_points:9.5,combined_available_max:13}]
};
assert.equal(unified.applyBulkFinancialPreview(display,bulkParts),1);
assert.equal(unified.pillarState(funded)[2].candidate,6);
assert.equal(unified.pillarState(funded)[2].available,8);
assert.equal(unified.pillarState(funded)[3].candidate,3.5);
assert.equal(unified.pillarState(funded)[3].available,5);
assert.equal(unified.pillarState(funded)[0].available,0);
assert.equal(unified.researchComposite(funded).earned,32.3);
assert.equal(unified.researchComposite(funded).available,38);
// Actual same-filing OpenDART FCF qualifies extra 4/5 subfactor points,
// never a new full 15pt financial/valuation score or a 100pt certification.
const fcfRow={
 code:funded.code,market:funded.market,asof:'2026-10-20',
 historical_PIT_certified:false,scoring_certified:false,
 strict_receipt_scope_matched:true,
 raw_observed_financial_fcf_assets_3y_pct:3.12,
 raw_observed_valuation_fcf_yield_pct:-1.75,
 financial_fcf_assets_candidate_points:2,
 financial_fcf_assets_available_max:4,financial_fcf_peer_count:33,
 valuation_fcf_yield_candidate_points:4,
 valuation_fcf_yield_available_max:5,valuation_fcf_peer_count:35
};
const fcf={
 schema:'v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1',
 asof:'2026-10-20',listed_total:2497,
 source_same_filing_scope_receipt_checked:true,raw_source_rows_stored:false,
 hist_PIT_verified:false,four_year_financial15_complete:false,
 valuation15_complete:false,joined_to_main_v3_ranking:false,
 buy_recommendations_authorized:false,six_pillar_confirmed_100pt:null,
 minimum_same_market_peers:30,financial_fcf_weight_available_max:4,
 valuation_fcf_weight_available_max:5,
 real_fcf_assets_three_year_raw_issuer_count:1,
 real_latest_fcf_yield_raw_issuer_count:1,
 financial_fcf4_research_candidate_issuer_count:1,
 valuation_fcf5_research_candidate_issuer_count:1,
 rows:[fcfRow]
};
display.marketEligible=2497;
assert.equal(unified.applyActualCashflowPreview(display,fcf),1);
assert.equal(unified.pillarState(funded)[2].candidate,8);
assert.equal(unified.pillarState(funded)[2].available,12);
assert.equal(unified.pillarState(funded)[3].candidate,7.5);
assert.equal(unified.pillarState(funded)[3].available,10);
assert.equal(unified.pillarState(funded)[0].candidate,null);
assert.equal(unified.researchComposite(funded).earned,38.3);
assert.equal(unified.researchComposite(funded).available,47);
assert.equal(unified.researchComposite(funded).complete,false);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,asof:'2026-10-21'}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[fcfRow,fcfRow]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[{...fcfRow,financial_fcf_assets_candidate_points:7}]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[{...fcfRow,valuation_fcf_peer_count:29}]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[{...fcfRow,scoring_certified:true}]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,financial_fcf4_research_candidate_issuer_count:2}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,buy_recommendations_authorized:true}),0);
assert.ok(source.includes('applyActualCashflowPreview(model,actualCashflowCoverage)'));
// A missing factor stays null, rather than being assigned zero points.
const unobserved={...funded,v3_dart_fcf_research:{
 ...fcfRow,financial_fcf_assets_candidate_points:null,
 financial_fcf_assets_available_max:0,
 valuation_fcf_yield_candidate_points:null,
 valuation_fcf_yield_available_max:0}};
assert.equal(unified.pillarState(unobserved)[2].available,8);
assert.equal(unified.pillarState(unobserved)[3].available,5);
const forged={...bulkParts,rows:[{...bulkParts.rows[0],
  financial_candidate_points:15,financial_available_max:8}]};
assert.equal(unified.applyBulkFinancialPreview(display,forged),0,
 'overweight financial research score should be blocked');
assert.equal(unified.applyBulkFinancialPreview(display,
 {...bulkParts,asof:'2026-10-21'}),0);
assert.equal(unified.applyBulkFinancialPreview(display,
 {...bulkParts,all_scores_provisional_unverified:false}),0);
assert.equal(unified.applyBulkFinancialPreview(display,
 {...bulkParts,rows:[bulkParts.rows[0],bulkParts.rows[0]]}),0,
 'a duplicate score row should invalidate the whole import');

const savedCoverage=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/v3-real-source-six-area-coverage.json'),'utf8'));
const actualSixText=unified.realSourceCoverageText(
 savedCoverage,savedCoverage.market_asof);
assert.ok(actualSixText.includes('6영역 계산가능 연구점수'));
assert.ok(actualSixText.includes('4영역 데이터 검증 시험군'));
assert.ok(actualSixText.includes('수급 0'));
assert.ok(actualSixText.includes(
 '재무 '+savedCoverage.six_pillar_numeric_provisional_issuer_counts.financial));
assert.equal(unified.realSourceCoverageText(
 savedCoverage,'2026-10-19'),'','old/future coverage source cannot blend into a market day');
assert.equal(unified.realSourceCoverageText(
 {...savedCoverage,six_pillar_official_complete_100pt_issuer_count:1},
 savedCoverage.market_asof),'','fabricated full V3 availability is rejected');
assert.equal(unified.realSourceCoverageText(
 {...savedCoverage,six_pillar_numeric_provisional_issuer_counts:{
  ...savedCoverage.six_pillar_numeric_provisional_issuer_counts,ias:25}},
 savedCoverage.market_asof),'','IAS cannot be inferred from other research evidence');
assert.equal(unified.realSourceCoverageText(null,savedCoverage.market_asof),'');

const actual=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
const dailyModel=legacy.prepare(actual,null,null,null,null,null);
const actualAll=unified.rankItems(dailyModel,legacy,'all');
assert.equal(actualAll.length,dailyModel.rows.length);
assert.ok(actualAll.every(x=>x.r.score>=0&&x.r.score<=100));
assert.ok(actualAll.every(x=>x.tech===null||x.tech<=25));
assert.equal(unified.summary(dailyModel,legacy).officialFullScore,0);
console.log('Unified workspace: V3 provisional sum, missing-data safety, refreshed research scoring, mobile source handling, real-market compatibility PASS; records='+actualAll.length);
