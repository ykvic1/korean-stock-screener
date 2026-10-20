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
assert.ok(html.includes('<option value="score" selected>기존 관측점수순</option>'),
 'default screen must NOT silently promote pattern-only priority above displayed score');
assert.ok(source.includes("sort='score'"),'default rank must use displayed score');
assert.ok(css.includes('color-scheme:dark')&&css.includes('.u-scoresix'),
 'approved dark-green rank concept and 6-pillar unknown table must be present');
assert.ok(source.includes('V3 합계')&&source.includes('미산출'),
 'do not label old observed ranking as official V3 composite');
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
const row2={...row,code:'000001',name:'위험 예시',stop_line:70,stop_pct:-30,score:95,
 pattern_research:null,pattern_weekly_research:null,v3_pattern_integrated:null};
const safe={r:row,q:null,watch:null,quant:false,researchCandidate:false};
const unsafe={r:row2,q:null,watch:null,quant:false,researchCandidate:false};
const model={date:'2026-10-20',rows:[unsafe,safe],marketEligible:2,
 rollingBookCount:0,quantCount:0};
const picked=unified.rankItems(model,legacy,'discover');
assert.deepEqual(picked.map(x=>x.r.code),['005930'],'discovery cannot promote high-risk higher legacy score');
assert.equal(unified.rankItems(model,legacy,'all').length,2);
assert.equal(unified.rankItems(model,legacy,'patterns').length,1);
assert.equal(unified.rankItems(model,legacy,'rsi').length,1);
assert.equal(unified.rankItems(model,legacy,'quant').length,0);
assert.equal(unified.rankItems(model,legacy,'all','all','위험').length,1);
assert.equal(unified.rankItems(model,legacy,'all','all','','score')[0].r.code,'000001',
 'user explicitly requested legacy score sort can still display unsafe rows with warning');
assert.deepEqual(unified.rankItems(model,legacy,'all','all','','research',true)
 .map(x=>x.r.code),['005930']);
assert.equal(unified.summary(model,legacy).officialFullScore,0,
 'no V3 final 100 or investment rank until all six pillars are certified');

// The reference mock visibly placed 65.7 (3rd), 51.4 (1st), 48.6 (2nd).
// With score-desc ranking it MUST instead be 65.7 -> 51.4 -> 48.6.
const designRows=[
 {r:{...row,code:'004490',name:'세방전지',score:65.7}},
 {r:{...row,code:'004370',name:'농심',score:51.4}},
 {r:{...row,code:'025860',name:'남해화학',score:48.6}},
].map(x=>({...safe,...x}));
const designModel={...model,rows:[designRows[1],designRows[2],designRows[0]]};
const ranked=unified.rankItems(designModel,legacy,'all');
assert.deepEqual(ranked.map(x=>x.r.name),['세방전지','농심','남해화학'],
 'always sort by the actual visible score, not input order or favorite pattern');
assert.deepEqual(ranked.map(x=>x.scoreRank),[1,2,3]);
assert.deepEqual(ranked.map(x=>unified.observedDisplayScore(x)),
 [65.7,51.4,48.6]);
// Rank numbers remain global within the same dated market regardless of
// RSI/pattern/market filters; do not relabel the #8 score as #1 when filtered.
const onlyNong=unified.rankItems(designModel,legacy,'all','all','농심');
assert.equal(onlyNong[0].scoreRank,2);
assert.deepEqual(
 unified.rankItems(designModel,legacy,'all','KOSPI').map(x=>x.scoreRank),
 [1,2,3]);
const tied={...safe,r:{...row,code:'000888',name:'점수동률',score:65.71,coverage_pct:63}};
const tiedModel={...designModel,rows:[...designRows,tied]};
const tiedRanks=unified.rankItems(tiedModel,legacy,'all');
assert.equal(tiedRanks[0].scoreRank,1);
assert.equal(tiedRanks[1].scoreRank,1,
 'same rendered one-decimal score gets the same visible competition rank');
assert.equal(tiedRanks[2].scoreRank,3);
// Explicit risk is disclosed, rather than silently re-ranking riskier
// high-score symbols; optional safer-only filter excludes them.
const riskSignals={...safe,r:{...row,code:'111111',name:'리스크종목',score:98,
 stop_line:60,stop_pct:-40}};
const riskyModel={...designModel,rows:[riskSignals,...designRows]};
const risky=unified.rankItems(riskyModel,legacy,'discover');
assert.equal(risky[0].r.code,'111111','do not hide high-score risk while calling list score-ranked');
assert.equal(risky[0].scoreRank,1);
assert.ok(risky[0].risk.unsafe,'high rank is NOT a buy signal');
assert.ok(!unified.rankItems(riskyModel,legacy,'discover','all','','score',true)
 .some(x=>x.r.code==='111111'),'downside-only filter works');
// TOP100 selects market-global score ranks, not the first 100 array entries.
const all106=Array.from({length:106},(_,i)=>({...safe,
 r:{...row,code:String(i).padStart(6,'0'),name:'합성'+i,
 score:100-i*0.2,coverage_pct:70}}));
const many={...designModel,rows:all106.reverse()};
const top=unified.rankItems(many,legacy,'all','all','','score',false,true);
assert.equal(top.length,100);
assert.equal(top[0].scoreRank,1);
assert.equal(top[99].scoreRank,100);
assert.ok(top.every((x,i)=>x.scoreRank===i+1));
const outsideTop=many.rows[119].r;
const globalSearch=unified.rankItems(
 many,legacy,'rsi','KOSDAQ',outsideTop.code,'score',true,true);
assert.deepEqual(globalSearch.map(x=>x.r.code),[outsideTop.code],
 'direct code search must find stock #120 with TOP100 on, RSI tab and safety filter');
assert.ok(globalSearch[0].scoreRank>100,
 'global search must still show real global legacy rank and risk disclosure');
const matchName=unified.rankItems(
 many,legacy,'quant','KOSDAQ',outsideTop.name,'score',true,true);
assert.ok(matchName.some(x=>x.r.code===outsideTop.code),
 'direct name lookup must search across whole market, not selected quant tab');
assert.ok(top.every(x=>unified.pillarState(x.r).find(z=>z.key==='ias').candidate===null),
 'market ranking must not fill missing investor flow to force complete V3 total');

const actual=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
const dailyModel=legacy.prepare(actual,null,null,null,null,null);
const actualAll=unified.rankItems(dailyModel,legacy,'all');
assert.equal(actualAll.length,dailyModel.rows.length);
assert.ok(actualAll.every(x=>x.r.score>=0&&x.r.score<=100));
assert.ok(actualAll.every(x=>x.tech===null||x.tech<=25));
assert.equal(unified.summary(dailyModel,legacy).officialFullScore,0);
console.log('Unified workspace: source-safe chart/day/week, source completeness, risk, legacy fallback, responsive layout, real-market compatibility PASS; records='+actualAll.length);
