'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const evidence=require('../docs/v3-stop-evidence.js');
const row={code:'000001',price_date:'2026-11-03',price:100,
 stop_line:90,stop_pct:-10,reference_resistance:120};
const before=JSON.stringify(row);
const current=evidence.profile(row,'2026-11-03');
assert.equal(current.sameDayReferenceReady,true);
assert.equal(current.stopDistancePct,10);
assert.equal(current.historicalHighRoomPct,20);
assert.equal(current.referenceRewardRisk,2);
assert.equal(current.historicalStopFirstPct,null);
assert.equal(current.verifiedHistoricalSampleCount,null);
assert.equal(current.scoringAllowed,false);
assert.match(evidence.markup(row,'2026-11-03'),/검증 표본 미연결/);
assert.equal(JSON.stringify(row),before);

const prior=evidence.profile({...row,price_date:'2026-11-02'},'2026-11-03');
assert.equal(prior.freshness,'prior');
assert.equal(prior.sameDayReferenceReady,false);
assert.equal(prior.stop,90,'dated prior reference is preserved');
assert.match(evidence.markup({...row,price_date:'2026-11-02'},'2026-11-03'),/이전 관측 유지/);

for(const patch of [{stop_line:null},{stop_line:100},{stop_line:101},
 {stop_pct:undefined},{stop_pct:-30},{price:NaN},{price:-1},{price:true}]){
 const state=evidence.profile({...row,...patch},'2026-11-03');
 assert.equal(state.sameDayReferenceReady,false);
 assert.equal(state.scoringAllowed,false);
 assert.equal(state.historicalStopFirstPct,null);
}
assert.equal(evidence.profile({...row,stop_pct:-11.9},'2026-11-03')
 .reportedDistanceConsistent,true,'same tolerance as existing stability overlay');
assert.equal(evidence.profile({...row,reference_resistance:99},'2026-11-03')
 .historicalHighRoomPct,null,'old high below current price is not an upside target');
assert.equal(evidence.profile({...row,price_date:'2026-11-04'},'2026-11-03').freshness,'future');
assert.equal(evidence.profile({...row,price_date:'2026-02-31'},'2026-11-03').freshness,'unknown');
assert.equal(evidence.profile(row,'invalid').sameDayReferenceReady,false);
const forged={...row,historicalStopFirstPct:99,verifiedHistoricalSampleCount:10000,
 scoringAllowed:true,regularSessionVerified:true,corporateActionsVerified:true};
assert.equal(evidence.profile(forged,'2026-11-03').historicalStopFirstPct,null);
assert.equal(evidence.profile(forged,'2026-11-03').regularSessionVerified,false);
assert.equal(evidence.profile(forged,'2026-11-03').corporateActionsVerified,false);
assert.equal(evidence.profile(forged,'2026-11-03').scoringAllowed,false);
const hostile=evidence.markup({...row,price_date:'<img src=x onerror=alert(1)>',
 stop_line:'<script>throw 1</script>'},'2026-11-03');
assert.doesNotMatch(hostile,/<script>|onerror=|NaN|undefined|Infinity/);
assert.match(evidence.markup({},'2026-11-03'),/미확인/);
assert.equal(evidence.profile({...row,price:1e-300,stop_line:1e-301,
 reference_resistance:1e308},'2026-11-03').historicalHighRoomPct,null);

for(const filename of ['index.html','workspace.html']){
 const html=fs.readFileSync(path.join(__dirname,'../docs',filename),'utf8');
 const addon=html.indexOf('v3-stop-evidence.js?');
 assert.ok(addon>=0&&addon<html.indexOf('<script src="workspace.js?'));
 assert.match(html,/workspace\.js\?v=v30b-20261104-eps-dated-fcf/);
}
const workspace=fs.readFileSync(path.join(__dirname,'../docs/workspace.js'),'utf8');
assert.ok(workspace.includes('root.V3StopEvidence.markup(r,model?.date)'));
console.log('Stop evidence: current/prior references, invalid levels, no invented probability, no score writes and safe markup PASS');
