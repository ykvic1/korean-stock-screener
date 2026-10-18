'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const watch=require('../docs/watchlist.js'),partial=require('../docs/partial_score.js');

const now=new Date('2026-10-18T10:30:00+09:00');
const score=(n,c=70)=>({normalized:n,coverage:c,available:c,earned:n*c/100});
const row=(code,more={})=>({
  code,name:'가상기업'+code,price:10000,stop_line:9400,stop_pct:-6,
  trading_eok:15,phase:'초기 돌파 확인 중',patterns:[],
  signals:[],risk_flags:[],ma120w_gap:5,reference_target_pct:100,
  reward_risk:5,_rating:score(80),...more
});
const snapshot=(stocks,date='2026-10-16')=>({status:'success',date,stocks});
const universe=Array.from({length:25},(_,i)=>row(String(100000+i),
  {_rating:score(60+i)}));
const all=watch.select(snapshot(universe),now);
assert.equal(all.rows.length,20);
assert.equal(all.featured_count,5);
assert.equal(all.additional_count,15);
assert.equal(all.rows[0].code,'100024');
assert.equal(all.rows[19].code,'100005');
assert.equal(all.eligible_count,25);
assert.equal(all.confirmedBuyRecommendation,null);

const low=row('200001',{_rating:score(81,85)});
const risky=row('200002',{_rating:score(91,90),
  stop_line:7700,stop_pct:-23,reference_target_pct:600,
  risk_flags:['예상 손절폭 과대']});
const rank=watch.select(snapshot([low,risky]),now);
assert.deepEqual(rank.rows.map(x=>x.code),['200002','200001']);
assert.equal(rank.priority.code,low.code);
assert.equal(rank.priority.analysis.recommendation,'심층분석 우선 후보');
assert.equal(rank.confirmedBuyRecommendation,null);
assert.equal(watch.profile(risky).risk,'high');
assert.equal(watch.profile(low).risk,'low');
for(const g of [-80,1,100,600]) {
 const p=watch.profile(row('203000',{reference_target_pct:g}));
 assert.equal(p.expectedReturnPct,null);
 assert.equal(p.expectedReturnVerified,false);
 assert.equal(p.highExpectedReturn,false);
 assert.equal(p.forecastRiskReturnKind,'unverified');
 assert.equal(p.readyToBuy,false);
}
const observed={
 code:'200001',asof:'2026-10-16',
 periods:{
  '스윙 1개월':{status:'exploratory',sample_n:120,symbol_n:16,
    quarter_n:18,mean_net_return_pct:5,stop_first_pct:30,
    max_adverse_p75_pct:9},
  '중기 3개월':{status:'exploratory',sample_n:80,symbol_n:15,
    quarter_n:14,mean_net_return_pct:-2,stop_first_pct:62,
    max_adverse_p75_pct:22},
  '중기 6개월':{status:'exploratory',sample_n:60,symbol_n:14,
    quarter_n:13,mean_net_return_pct:-6,stop_first_pct:67,
    max_adverse_p75_pct:30}
 }
};
const short=watch.profile(row('200001',{phase:'상승 후 수렴'}),observed,'2026-10-16');
assert.equal(short.horizon,'1m');
assert.equal(short.historical['1m'].meanHistoricalNetPct,5);
assert.equal(short.historical['3m'].meanHistoricalNetPct,-2);
assert.equal(short.historical['1m'].validatedForwardPrediction,false);
assert.ok(short.longHoldingCaution.includes('3개월'));
assert.equal(short.expectedReturnPct,null);
assert.equal(watch.profile(row('200001',{phase:'상승 후 수렴'}),
  observed,'2026-10-17').horizon,'3m'); // old quant cannot override
const middle={
 ...observed,periods:{
  ...observed.periods,
  '중기 3개월':{...observed.periods['중기 3개월'],mean_net_return_pct:4},
  '중기 6개월':{...observed.periods['중기 6개월'],mean_net_return_pct:-5}
 }
};
const medium=watch.profile(row('200001',{phase:'장기 추세 전환 관찰'}),middle,'2026-10-16');
assert.equal(medium.horizon,'3m');
assert.ok(medium.longHoldingCaution.includes('6개월'));

assert.equal(watch.profile(row('001001')).horizon,'1m');
assert.equal(watch.profile(row('001002',{phase:'상승 후 수렴'})).horizon,'3m');
assert.equal(watch.profile(row('001003',{phase:'장기 추세 전환 관찰'})).horizon,'6m');
assert.equal(watch.profile(row('001004',{ma120w_gap:-20,
  risk_flags:['장기 추세 취약']})).horizon,'1m');
assert.ok(watch.profile(row('001004',{ma120w_gap:-20,
  risk_flags:['장기 추세 취약']})).longHoldingCaution);
assert.equal(watch.profile(row('001005',{phase:'중립/기타'})).horizon,'unknown');
assert.equal(watch.profile(row('001006',{stop_line:null,stop_pct:null})).risk,'unknown');
assert.equal(watch.profile(row('001007',{stop_line:7000,stop_pct:-6})).risk,'unknown');
assert.equal(watch.profile(row('001008',{trading_eok:0.1})).risk,'high');
assert.equal(watch.profile(row('001009',{stop_line:9800,stop_pct:-2})).risk,'high');
assert.equal(watch.profile(row('001010',{stop_line:8800,stop_pct:-12})).risk,'middle');

// Each 'high' result must say WHY. A stock with an ordinary 6% stop is
// not inherently high *stop risk* merely because the trend is stretched
// or a liquidity exit would be difficult.
const chase=watch.profile(row('002001',{stop_line:9400,stop_pct:-6,
  risk_flags:['52주 고점권 — 추격 주의']}));
assert.equal(chase.risk,'high');
assert.equal(chase.riskBreakdown.stop,'손절폭 짧음');
assert.ok(chase.riskLabel.includes('급등·추격'));
assert.ok(!chase.riskLabel.includes('손절폭 큼'));
assert.ok(chase.riskReasons.some(x=>x.includes('추격')));
const liquidity=watch.profile(row('002002',{stop_line:9400,stop_pct:-6,
  trading_eok:0.45}));
assert.equal(liquidity.risk,'high');
assert.equal(liquidity.riskBreakdown.stop,'손절폭 짧음');
assert.ok(liquidity.riskLabel.includes('거래대금 부족'));
assert.ok(liquidity.riskBreakdown.liquidity.includes('2억원 미만'));
const stopWide=watch.profile(row('002003',{stop_line:8100,stop_pct:-19}));
assert.equal(stopWide.risk,'high');
assert.equal(stopWide.riskBreakdown.stop,'손절폭 과대 · 하방 위험');
assert.ok(stopWide.riskReasons.includes('손절폭 큼'));
const stopTight=watch.profile(row('002004',{stop_line:9900,stop_pct:-1}));
assert.equal(stopTight.risk,'high');
assert.ok(stopTight.riskReasons.includes('손절선 과도하게 근접'));
const noWarning=watch.profile(row('002005',{stop_line:9400,stop_pct:-6}));
assert.equal(noWarning.risk,'low');
assert.equal(noWarning.riskReasons.length,0);
assert.ok(noWarning.riskLabel.includes('별도 진입 경고 없음'));
assert.equal(noWarning.riskBreakdown.stop,'손절폭 짧음');
const unknownStop=watch.profile(row('002006',{stop_line:null,stop_pct:null,
  risk_flags:['52주 고점권 — 추격 주의']}));
assert.equal(unknownStop.risk,'unknown');
assert.equal(unknownStop.riskBreakdown.stop,'손절 기준 미확인');
assert.ok(unknownStop.riskReasons.some(x=>x.includes('추격')));

assert.deepEqual(watch.select(snapshot([low,risky]),now,{risk:'high'})
  .rows.map(x=>x.code),['200002']);
assert.deepEqual(watch.select(snapshot([low,risky]),now,{risk:'low'})
  .rows.map(x=>x.code),['200001']);
assert.equal(watch.select(snapshot([low]),now,{risk:'high-high'}).status,'invalid_filter');
assert.equal(watch.select(snapshot([low]),now,{horizon:'6m'}).rows.length,0);
assert.equal(watch.select(snapshot([low]),new Date('2026-10-26T12:00:00+09:00')).status,'stale_or_unavailable');
assert.equal(watch.select(snapshot([low,low]),now).rows.length,1);
assert.equal(watch.candidate(row('300001',{_rating:null})),false);
assert.equal(watch.candidate(row('300002',{_rating:score(110)})),false);
assert.equal(watch.candidate(row('300003',{_rating:score(70,0)})),false);
assert.equal(watch.candidate(row('300004',{price:null})),false);
assert.equal(watch.candidate(row('invalid')),false);

const frozen=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
if(frozen.status==='success'&&frozen.stocks?.length) {
 const census={risk:{},warning_causes:{},examples:[]};
 for(const x of frozen.stocks){
   x._rating=partial.rating(x,frozen.weights);
   const p=watch.profile(x);
   census.risk[p.risk]=(census.risk[p.risk]||0)+1;
   for(const reason of p.riskReasons)census.warning_causes[reason]=(census.warning_causes[reason]||0)+1;
   if(census.examples.length<8&&p.risk==='high')
     census.examples.push({name:x.name,stop:x.stop_pct,stopCategory:p.riskBreakdown.stop,
       riskLabel:p.riskLabel});
 }
 console.log('REAL_PRICE_STOCK_RISK_CENSUS '+JSON.stringify({
   asof:frozen.date,stocks:frozen.stocks.length,...census
 }));
 const day=new Date(frozen.date+'T18:30:00+09:00');
 const sample=watch.select(frozen,new Date(day.getTime()+3600000));
 assert.ok(sample.rows.length<=20);
 assert.equal(sample.confirmedBuyRecommendation,null);
 for(let i=1;i<sample.rows.length;i++)
   assert.ok(watch.compare(sample.rows[i-1],sample.rows[i])<=0);
}
const html=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
for(const phrase of ['진입 위험 (구체적 원인은 종목별 표시)','적합 접근기간 (잠정)',
  '단기 접근 · 약 1개월','중기 접근 · 약 3개월','장기 검토 · 6개월 이상',
  '과거 유사조건 순수익:','미래 기대수익 미산출',
  '장기 보유 주의:','가장 먼저 심층분석할 종목']) {
 assert.ok(html.includes(phrase),phrase);
}
assert.ok(html.includes("profile?.riskLabel"));
assert.ok(!html.includes("profile?.label||'위험·상승공간 미확인'"));
assert.ok(html.includes('손절 기준:'));
assert.ok(html.includes('환금성:'));
assert.ok(html.includes('riskBreakdown?.stop'));
assert.ok(!html.includes('손절·추격·환금위험 높음'));
assert.ok(!html.includes('낮은 손절폭 · 고점거리 큼'));
assert.ok(!html.includes('높은 위험 · 고점거리 큼'));
assert.ok(html.includes('<div class="quick-main"><b>'));
const css=html.split('<style>')[1]?.split('</style>')[0]||'';
assert.match(css,/@media\(max-width:760px\)\{\s*\.quick-item\{grid-template-columns:minmax\(0,1fr\)/);
assert.ok(css.includes('.quick-actions{grid-column:2;grid-row:1 / span 2'));
console.log('risk_horizon_score_ranked_watchlist_offline_tests_passed');
