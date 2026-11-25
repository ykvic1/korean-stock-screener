'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {view,selectRows}=require('../docs/v32-market-sector.js');
const read=p=>JSON.parse(fs.readFileSync('docs/'+p,'utf8'));
const summary=read('v3-current-observed-all-listed-summary.json'),raw=read('v3-industry-observed-relative-strength.json'),official=read('official.json');
// Actual saved snapshot; current or explicitly stale, never silently promoted.
const aligned={...summary,asof:raw.asof,listed_total:raw.listed_total};
const a=view(aligned,raw,official);
assert.equal(a.rows.length,raw.groups_total);
assert.equal(a.sector.paired,raw.paired_20_60_observations);
assert.equal(selectRows(a,'all','m20','recovery').length,raw.groups_early_recovery_watch);
assert.ok(selectRows(a,'KOSPI','m60').every(r=>r.market==='KOSPI'));
const sorted=selectRows(a,'all','m120');let last=Infinity;
for(const r of sorted){const n=r.m120??-Infinity;assert.ok(n<=last);last=n;}
assert.equal(view({...aligned,asof:'2099-01-01'},raw,official).sector.stale,true);
assert.equal(view({...aligned,asof:'2000-01-01'},raw,official).sector,null);
assert.equal(view({...aligned,listed_total:raw.listed_total+1},raw,official).sector,null);
assert.equal(view(aligned,{...raw,groups:[...raw.groups,raw.groups[0]],groups_total:raw.groups_total+1},official).sector,null);
assert.equal(view(aligned,raw,{krx:{status:'unconfirmed',asof:raw.asof}}).indexStatus,'공식 코스피·코스닥 지수 확인 대기');
assert.equal(selectRows(a,'all','bad').length,0);
console.log('V3.2 saved sector view: source, freshness, sorting, coverage and missingness PASS');
