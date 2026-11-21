'use strict';
// Exercise the actual Pages attachment and detail renderer on saved inputs.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
const operational=require('../docs/operational-score.js');
const workspace=require('../docs/workspace.js');

function regularCloseEvidence(docs,model,marketBytes,pricedCount){
 const file=path.join(docs,'v3-regular-close-prices.json');
 if(!fs.existsSync(file))return {status:'unavailable',linked:0};
 const report=JSON.parse(fs.readFileSync(file,'utf8'));
 const digest=createHash('sha256').update(marketBytes).digest('hex');
 assert.match(report.asof,/^\d{4}-\d{2}-\d{2}$/,'regular-close date');
 assert.ok(report.asof<=model.date,'future regular-close report');
 if(report.asof<model.date){
  assert.equal(workspace.applyRegularClosePrices(model,report,digest),0,'prior-day close excluded');
  return {status:'awaiting_current_market_day',asof:report.asof,linked:0};
 }
 assert.match(report.source_market_sha256,/^[a-f0-9]{64}$/,'regular-close market digest');
 if(report.source_market_sha256!==digest){
  assert.equal(workspace.applyRegularClosePrices(model,report,digest),0,'superseded close excluded');
  return {status:'awaiting_current_market_snapshot',asof:report.asof,linked:0};
 }
 assert.equal(report.requested_count,pricedCount,'regular-close priced universe');
 assert.ok(Number.isSafeInteger(report.matched_count)&&report.matched_count>=0&&
  report.matched_count<=pricedCount,'regular-close matched count');
 assert.equal(Object.keys(report.rows).length,report.matched_count,'regular-close quote count');
 const linked=workspace.applyRegularClosePrices(model,report,digest);
 assert.equal(linked,report.matched_count,'regular-close UI attachment');
 return {status:linked?'current':'no_matching_quotes',asof:report.asof,linked,requested:pricedCount};
}
function audit(docs){
 const read=n=>JSON.parse(fs.readFileSync(path.join(docs,n),'utf8'));
 const marketBytes=fs.readFileSync(path.join(docs,'data.json'));
 const market=JSON.parse(marketBytes),report=read('v3-operational-research-scores.json');
 const model={date:market.date,marketEligible:market.eligible_count,
  rows:market.stocks.map(r=>({r:{...r}}))};
 assert.equal(report.asof,model.date,'market/score date');
 assert.equal(report.listed_total,model.marketEligible,'market/score universe');
 const attached=operational.attachAllListed(model,report);
 assert.equal(attached.stale,false,'current roster');
 assert.equal(attached.blocked,0,'issuer identity');
 assert.equal(attached.missing,0,'issuer score link');
 assert.equal(attached.linked,report.listed_total,'all-listed attachment');
 assert.equal(model.rows.length,report.listed_total,'all-listed display');
 let ranked=0,unobserved=0;
 for(const {r} of model.rows){
  const source=report.rows[r.code],v=r.v3_operational;
  assert.strictEqual(v,source,'same score object in details');
  assert.equal(operational.validRow(v,report.asof,report.version),true,'detail validator');
  const composite=operational.composite(r);
  assert.equal(composite.available,source.available,'displayed available');
  assert.equal(composite.earned,source.base,'displayed total');
  assert.ok(operational.markup(r).includes('잠정 운영점수'),'rendered details');
  if(v.score===null){
   assert.equal(v.rank,null,'unobserved issuer rank');unobserved++;
  }else{
   assert.equal(v.stop_adjustment,0,'stop adjustment removed');ranked++;
  }
  if(r.unpriced_operational_only)assert.equal(r.price,null,'missing price remains null');
 }
 const ordered=model.rows.filter(x=>x.r.v3_operational.score!==null).sort(operational.compare);
 ordered.forEach((x,i)=>assert.equal(x.r.v3_operational.rank,i+1,'UI rank order'));
 return {status:'coherent',linked:attached.linked,unpriced:attached.unpriced,
  ranked,unobserved,detail_rows_checked:model.rows.length,
  regular_close:regularCloseEvidence(docs,model,marketBytes,market.stocks.length)};
}
module.exports={audit};
if(require.main===module){
 try{process.stdout.write(JSON.stringify(audit(process.argv[2]||'docs'))+'\n');}
 catch(e){process.stderr.write(e.message+'\n');process.exitCode=2;}
}
