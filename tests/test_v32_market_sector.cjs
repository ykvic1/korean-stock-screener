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

const dual=read('v3-industry-dual-evidence-watch.json');
const joined=view(aligned,raw,official,dual);
if(dual.asof===raw.asof&&dual.source_sha256.catalog===raw.source_sha256.catalog&&dual.source_sha256.current_dart_company_industry===raw.source_sha256.current_dart_company_industry_codes){
 assert.equal(joined.profit.total,dual.two_year_profit_verified_samples);
 assert.equal(selectRows(joined,'all','m20','dual').length,dual.dual_evidence_watch_groups);
 assert.equal(joined.rows.length,raw.groups_total);
}
const invalid=view(aligned,raw,official,{...dual,asof:'2000-01-01'});
assert.equal(invalid.profit,null);assert.equal(invalid.rows.length,raw.groups_total);
assert.equal(selectRows(invalid,'all','m20','dual').length,0);
assert.equal(view(aligned,raw,official,{...dual,source_sha256:{...dual.source_sha256,catalog:'0'.repeat(64)}}).profit,null);
assert.equal(view(aligned,raw,official,{...dual,groups:[...dual.groups.slice(1),dual.groups[1]]}).profit,null);
console.log('V3.2 profit evidence join: source dates, counts and fail-closed validation PASS');

const {marketRegime,indexEvidence,flowEvidence}=require('../docs/v32-market-sector.js');
assert.equal(marketRegime({'20':1,'60':2,'120':3}),'상승 추세 지속');
assert.equal(marketRegime({'20':1,'60':-2,'120':-3}),'단기 반등·회복 관찰');
assert.equal(marketRegime({'20':-1,'60':2,'120':3}),'상승 중 조정');
assert.equal(marketRegime({'20':-1,'60':-2,'120':-3}),'하락 추세 지속');
assert.equal(marketRegime({'20':0,'60':2,'120':3}),'혼조·전환 관찰');
assert.equal(marketRegime({'20':1,'60':null,'120':3}),'자료 부족');
const d=summary.asof,ref={schema:'v32-market-index-reference-1',asof:d,source:'FinanceDataReader KS11/KQ11 (secondary index reference)',official_krx_index_certified:false,new_provider_calls:0,markets:{KOSPI:{asof:d,return_pct:{'20':1,'60':2,'120':null}}}};
assert.equal(indexEvidence(d,ref).length,1);
assert.equal(indexEvidence(d,ref)[0].regime,'자료 부족');
assert.equal(indexEvidence(d,{...ref,asof:'2000-01-01'}).length,0);
assert.equal(indexEvidence(d,{...ref,official_krx_index_certified:true}).length,0);
assert.equal(indexEvidence(d,undefined).length,0);
const cov=read('v22-coverage.json');
const alignedCoverage=flowEvidence(cov.asof,cov.market_listing_size,cov);
assert.equal(alignedCoverage.legacyConfirmed,cov.flow_confirmed_count);
assert.equal(flowEvidence(d,summary.listed_total,{...cov,asof:'2000-01-01'}),null);
assert.equal(flowEvidence(cov.asof,cov.market_listing_size,{...cov,foreign_institution_250_day_history_verified:true}),null);
console.log('V3.2 market regime and flow coverage: same date, null and source validation PASS');
