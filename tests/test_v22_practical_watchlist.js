'use strict';
// Tests only, synthetic stocks: no API access or implied realized returns.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const watch = require('../docs/watchlist.js');
const partial = require('../docs/partial_score.js');

const now=new Date('2026-10-18T10:30:00+09:00');
const rate=(score,coverage=70)=>({
  normalized:score,coverage,available:coverage,
  earned:score*coverage/100,provisional:true
});
const base=(code,extras={})=>({
  code,name:'가상종목-'+code,
  price:10000,stop_line:9400,stop_pct:-6,
  phase:'초기 돌파 확인 중',patterns:[],
  obv_rising:true,price20:5,price60:9,
  reference_target_pct:30,reward_risk:2.5,
  trading_eok:30,risk_flags:[],ma120w_gap:null,
  signals:[],_rating:rate(73),
  ...extras
});
const snapshot=(rows,day='2026-10-16')=>({
  version:'2.0',status:'success',date:day,stocks:rows
});
const stocks=Array.from({length:25},(_,i)=>base(
  String(100000+i),{_rating:rate(60+i)}
));
const list=watch.select(snapshot(stocks),now);
assert.equal(watch.MAX_LISTED,20);
assert.equal(watch.MAX_FEATURED,5);
assert.equal(list.status,'score_ranked_research_only');
assert.equal(list.rows.length,20);
assert.equal(list.featured_count,5);
assert.equal(list.additional_count,15);
assert.equal(list.eligible_count,25);
assert.equal(list.filtered_count,25);
assert.equal(list.rows[0].code,'100024'); // score 84 ranks ahead
assert.equal(list.rows[19].code,'100005');
assert.equal(new Set(list.rows.map(r=>r.code)).size,20);

// Score is ALWAYS the primary ranking key; risk/phase can't silently reorder it.
const good=base('200001',{_rating:rate(81,80),stop_line:9400,stop_pct:-6});
const risky=base('200002',{_rating:rate(90,80),stop_line:7500,
  stop_pct:-25,reference_target_pct:50,reward_risk:2.0,
  risk_flags:['예상 손절폭 과대'],price60:50});
const mixed=watch.select(snapshot([good,risky]),now);
assert.deepEqual(mixed.rows.map(r=>r.code),['200002','200001']);
assert.equal(watch.profile(risky).kind,'high-high');
assert.equal(watch.profile(good).kind,'low-high');
assert.equal(watch.profile(risky).expectedReturnVerified,false);
assert.equal(watch.profile(good).timeForecastVerified,false);

const sorted=watch.select(snapshot([
  base('000001',{_rating:rate(90,40)}),
  base('000002',{_rating:rate(90,95)}),
  base('000003',{_rating:rate(89,100)})
]),now);
assert.deepEqual(sorted.rows.map(r=>r.code),['000002','000001','000003']);

const profiles=[
  [base('001001',{reference_target_pct:8,reward_risk:1.1}),'low-low'],
  [base('001002',{stop_pct:-22,stop_line:7800,reference_target_pct:42,
                   reward_risk:2.0}),'high-high'],
  [base('001003',{stop_pct:-20,stop_line:8000,reference_target_pct:5,
                   reward_risk:0.25}),'high-low'],
  [base('001004',{stop_pct:-12,stop_line:8800,reference_target_pct:18,
                   reward_risk:1.5}),'balanced'],
  [base('001005',{stop_pct:null,stop_line:null,reference_target_pct:null,
                   reward_risk:null}),'unknown'],
  [base('001006',{stop_pct:-6,stop_line:7000}),'unknown'],
  [base('001007',{stop_pct:-6,stop_line:9400,trading_eok:0.2,
                   reference_target_pct:44,reward_risk:2}),'high-high'],
  [base('001008',{stop_pct:-6,stop_line:9400,risk_flags:['단기 급등 — 추격 주의'],
                   reference_target_pct:5,reward_risk:0.5}),'high-low']
];
for(const [row,kind] of profiles){
  assert.equal(watch.profile(row).kind,kind,row.code);
  assert.equal(watch.candidate(row),true,
    'never exclude stocks only because of high risk, small turnover, or missing stop');
}

assert.equal(watch.profile(base('001010')).horizon,'1m');
assert.equal(watch.profile(base('001011',{phase:'상승 후 수렴',
  patterns:['상승 후 횡보·수렴 후보']})).horizon,'3m');
assert.equal(watch.profile(base('001012',{phase:'장기 추세 전환 관찰'})).horizon,'6m');
assert.equal(watch.profile(base('001013',{phase:'중립/기타',
  signals:['주봉 120주선 회복']})).horizon,'6m');
assert.equal(watch.profile(base('001014',{phase:'중립/기타'})).horizon,'unknown');

assert.deepEqual(watch.select(snapshot([
  good,risky,
  base('200003',{phase:'상승 후 수렴',patterns:['상승 후 수렴 후보'],
                reference_target_pct:9,reward_risk:1.0,_rating:rate(88)}),
  base('200004',{phase:'장기 추세 전환 관찰',_rating:rate(85)})
]),now,{kind:'high-high',horizon:'1m'}).rows.map(x=>x.code),['200002']);
assert.deepEqual(watch.select(snapshot([
  good,risky,base('200004',{phase:'장기 추세 전환 관찰',_rating:rate(85)})
]),now,{horizon:'6m'}).rows.map(x=>x.code),['200004']);
assert.equal(watch.select(snapshot([good]),now,{kind:'not-real'}).status,'invalid_filter');
assert.equal(watch.select(snapshot([good]),now,{kind:'high-low'}).status,'no_matching_observations');
assert.equal(watch.select(snapshot([good]),new Date('2026-10-26T12:00:00+09:00')).status,
  'stale_or_unavailable');
assert.equal(watch.select({status:'failed',date:'2026-10-16',stocks:[good]},now).rows.length,0);

assert.equal(watch.candidate(base('200005',{_rating:null})),false);
assert.equal(watch.candidate(base('200006',{_rating:rate(110)})),false);
assert.equal(watch.candidate(base('200007',{_rating:rate(80,0)})),false);
assert.equal(watch.candidate(base('200008',{name:''})),false);
assert.equal(watch.candidate(base('200009',{code:'invalid'})),false);
assert.equal(watch.candidate(base('200010',{price:null})),false);
assert.equal(watch.candidate(base('200011',{_rating:rate(NaN)})),false);
assert.deepEqual(watch.select(snapshot([good,good]),now).rows.map(x=>x.code),['200001']);

// Existing v2.2 published data: only count scores that validate the actual
// component+weight contract. Never require live Npay/official KRX flow.
const actual=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
if(actual.status==='success'&&Array.isArray(actual.stocks)&&actual.date){
  for(const row of actual.stocks)row._rating=partial.rating(row,actual.weights);
  const frozen=new Date(actual.date+'T18:30:00+09:00');
  const observations=watch.select(actual,new Date(frozen.getTime()+3600000));
  assert.ok(observations.rows.length<=20);
  assert.equal(observations.status,observations.rows.length?
               'score_ranked_research_only':'no_matching_observations');
  for(let i=1;i<observations.rows.length;i++){
    assert.ok(watch.compare(observations.rows[i-1],observations.rows[i])<=0);
  }
}
const html=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
for(const marker of ['관심종목 점수 상위 20개','watchKind','watchHorizon',
  '위험 · 참고 상승여력','단기 · 1개월','중기 · 3개월','장기 · 6개월 이상',
  '고수익/기간 달성 예측 아님','52주 고점까지 거리']){
  assert.ok(html.includes(marker),marker);
}
assert.ok(html.includes('<script src="watchlist.js"></script>'));
assert.ok(html.includes('renderPractical(d);'));
console.log('risk_horizon_score_ranked_watchlist_offline_tests_passed');
