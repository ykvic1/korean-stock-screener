'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const x=require('../docs/v3-beta.js');
const data=(stocks,overrides={})=>({
 version:'2.0',status:'success',date:'2026-10-16',stocks,
 ...overrides
});
const rows=[
 {code:'043370',name:'실험초기',market:'KOSDAQ',price:11770,stop_line:10380,stop_pct:-11.8,
  phase:'초기 돌파 확인 중',patterns:['역헤드앤숄더'],one_day_volume_ratio:5.7,volume_ratio:1.6,obv_rising:false,signals:['주봉 120주선 회복'],ma120w_gap:5.5},
 {code:'005930',name:'보통주',market:'KOSPI',price:60000,stop_line:null,phase:'조정 중',patterns:[],obv_rising:true,one_day_volume_ratio:0.7,volume_ratio:0.9},
 {code:'246000',name:'불일치',market:'KOSDAQ',price:20000,stop_line:10000,stop_pct:-3,phase:'초기 전환',patterns:[]}
];
let p=x.parseSnapshot(data(rows));
assert.equal(p.stocks.length,3);
assert.equal(p.date,'2026-10-16');
assert.equal(x.priceRisk(rows[0]),11.81);
assert.equal(x.priceRisk(rows[1]),null);
assert.equal(x.priceRisk(rows[2]),null,'contradictory stop percentage cannot pass low risk filter');
assert.equal(x.eligible(rows[0],{kind:'weekly',risk:'all',search:''}),true);
assert.equal(x.eligible(rows[0],{kind:'pattern',risk:'controlled',search:'초기'}),true);
assert.equal(x.eligible(rows[0],{kind:'obv',risk:'all',search:''}),false);
assert.equal(x.eligible(rows[1],{kind:'obv',risk:'all',search:''}),true);
assert.equal(x.eligible(rows[2],{kind:'all',risk:'controlled',search:''}),false);
assert.equal(x.eligible(rows[1],{kind:'all',risk:'unverified',search:'005'}),true);
assert.deepEqual(x.rankRows(rows).map(v=>v.code),['043370','005930','246000']);
assert.equal(x.parseSnapshot(data([...rows,rows[0]])).stocks.length,3,'duplicate stock code ignored');
assert.throws(()=>x.parseSnapshot(data([])));
assert.throws(()=>x.parseSnapshot(data(rows,{date:'2026-02-31'})));
assert.throws(()=>x.parseSnapshot(data(rows,{status:'error'})));
assert.throws(()=>x.parseSnapshot(data(rows,{version:'3.0'})),'should never treat V2 data as V3 official');
assert.equal(x.isStale('2026-10-16',Date.parse('2026-10-19T07:00:00Z')),false);
assert.equal(x.isStale('2026-10-16',Date.parse('2026-10-24T07:00:00Z')),true);
const tracked=x.makeRecord(rows[0],'2026-10-16');
assert.ok(tracked);
assert.equal(tracked.entry,11770);
assert.equal(tracked.stop,10380);
assert.equal(tracked.source,'v2_public_price_snapshot');
const observed=x.markToMarket(tracked,{'043370':{price:12000}},'2026-10-19',Date.parse('2026-10-19T07:00:00Z'));
assert.equal(observed.gainPct,1.95);
assert.equal(observed.stopCloseBroken,false);
const violated=x.markToMarket(tracked,{'043370':{price:10370}},'2026-10-19',Date.parse('2026-10-19T07:00:00Z'));
assert.equal(violated.stopCloseBroken,true);
assert.equal(x.markToMarket(tracked,{},'2026-10-19',Date.parse('2026-10-19T07:00:00Z')).gainPct,null);
assert.equal(x.markToMarket(tracked,{'043370':{price:9999}},'2026-10-24',Date.parse('2026-10-30T07:00:00Z')).gainPct,null,'stale values cannot become forward performance');
assert.equal(x.markToMarket(tracked,{'043370':{price:9999}},'2026-10-15',Date.parse('2026-10-15T07:00:00Z')).gainPct,null,'past observations cannot be future returns');
assert.equal(x.safeRecord({date:'2026-10-16',code:'043370',entry:10,stop:20}),null);
assert.equal(x.escapeHtml('<img src=x onerror=alert(1)>'),'&lt;img src=x onerror=alert(1)&gt;');
const page=fs.readFileSync(path.join(__dirname,'../docs/v3-beta.html'),'utf8');
assert.ok(page.includes('v3 사용자 모의검증 베타'));
assert.ok(page.includes('기존 v2.2 공개 일별 분석값'));
assert.ok(page.includes('브라우저'));
assert.ok(page.includes('v3-beta.js')&&page.includes('v3-beta.css'));
assert.ok(!page.includes('실전 종목 추천 확정'));
console.log('v3 paper beta: all offline source, risk, filtering, PIT-date and security assertions passed');
