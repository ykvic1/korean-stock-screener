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
assert.equal(Object.values(unified.WEIGHTS).reduce((a,b)=>a+b,0),100);
assert.deepEqual(unified.WEIGHTS,{ias:20,technical:25,financial:15,valuation:15,industry:15,momentum:10});
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
 '시장 · 정렬 · 손절 위험 상세 설정','V3 6영역 종합점수','차트 패턴'])
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
assert.equal(unified.researchComposite(partial3).earned,16.5);
assert.equal(unified.researchComposite(partial3).available,36,
 'other V3 candidates may join when already independently supplied for the same date');
assert.equal(unified.researchComposite({...partial3,
 v3_pattern_integrated:{...partial3.v3_pattern_integrated,
 six_pillar_maximum:{...unified.WEIGHTS,financial:25}}
}).earned,6.5,'out-of-contract candidate maxima cannot inflate a total');

const highLegacy={...row,code:'000001',name:'높은 구형점수',
 score:98,v3_pattern_integrated:null};
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

const actual=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
const dailyModel=legacy.prepare(actual,null,null,null,null,null);
const actualAll=unified.rankItems(dailyModel,legacy,'all');
assert.equal(actualAll.length,dailyModel.rows.length);
assert.ok(actualAll.every(x=>x.r.score>=0&&x.r.score<=100));
assert.ok(actualAll.every(x=>x.tech===null||x.tech<=25));
assert.equal(unified.summary(dailyModel,legacy).officialFullScore,0);
console.log('Unified workspace: V3 provisional sum, missing-data safety, refreshed research scoring, mobile source handling, real-market compatibility PASS; records='+actualAll.length);
