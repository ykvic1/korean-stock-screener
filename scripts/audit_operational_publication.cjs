'use strict';
// Exercise the actual Pages attachment and detail renderer on saved inputs.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const operational=require('../docs/operational-score.js');
function audit(docs){
 const read=n=>JSON.parse(fs.readFileSync(path.join(docs,n),'utf8'));
 const market=read('data.json'),report=read('v3-operational-research-scores.json');
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
  ranked,unobserved,detail_rows_checked:model.rows.length};
}
module.exports={audit};
if(require.main===module){
 try{process.stdout.write(JSON.stringify(audit(process.argv[2]||'docs'))+'\n');}
 catch(e){process.stderr.write(e.message+'\n');process.exitCode=2;}
}
