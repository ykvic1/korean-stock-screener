'use strict';
const assert=require('node:assert/strict');
const status=require('../docs/v3-trading-status.js');
const model={date:'2026-11-09',marketEligible:3,rows:[
 {r:{code:'000001',name:'A',market:'KOSPI',price:null}},
 {r:{code:'000002',name:'B',market:'KOSDAQ',price:1900}},
 {r:{code:'000003',name:'C',market:'KOSDAQ',price:1800}},
]};
const counts={zero_volume:1,trade_observed:1,volume_unknown:1,
 short_halt_1_to_2_observed_sessions:1,extended_halt_3_plus_observed_sessions:0,
 unknown_halt_duration:0};
const report={schema:'v3-trading-status-display-v1',asof:'2026-11-09',
 listed_total:3,default_state:'trade_observed',krx_regular_session_verified:false,score_points_added:0,
 counts,rows:{
 '000001':{name:'A',market:'KOSPI',state:'zero_volume',duration:'short_1_to_2',
  observed_zero_streak:2,reason:null,official_reason_verified:false},
 '000003':{name:'C',market:'KOSDAQ',state:'volume_unknown',duration:null,
  observed_zero_streak:null,reason:null,official_reason_verified:false},
 }};
assert.equal(status.attach(model,report).halted,1);
assert.equal(status.label(model.rows[0].r),'단기 거래정지 · 2거래일 관측');
assert.equal(status.label(model.rows[1].r),null);
assert.equal(model.rows.length,3);
assert.equal(model.rows[0].r.price,null);
assert.equal(model.rows[1].r.price,1900);
assert.equal(status.attach(model,{...report,asof:'2026-11-06'}),null);
assert.equal(status.attach(model,{...report,rows:{...report.rows,'000001':{
 ...report.rows['000001'],name:'다른회사'}}}),null);
assert.equal(status.attach(model,{...report,counts:{...counts,zero_volume:2}}),null);
const unknown=JSON.parse(JSON.stringify(report));
unknown.rows['000001'].observed_zero_streak=null;
unknown.rows['000001'].duration='unknown';
unknown.counts.short_halt_1_to_2_observed_sessions=0;
unknown.counts.unknown_halt_duration=1;
assert.equal(status.attach(model,unknown).durationUnknown,1);
assert.equal(status.label(model.rows[0].r),'거래정지 · 기간 미확인');
console.log('trading status display classification: passed');
