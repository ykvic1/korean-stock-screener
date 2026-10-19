'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const core=require('../docs/v3-beta.js');

const basic={version:'2.0',status:'success',date:'2026-10-19',stocks:[{
 code:'005930',name:'기존 검증주',market:'KOSPI',price:80000,stop_line:65000,
}]};
const current=core.parseSnapshot(basic);
const catalogRows=[
 {code:'005930',name:'기존 검증주',market:'KOSPI',price_history_status:'priced',
  published_price:80000,reference_market_cap_eok:350000,
  reference_trading_value_eok:1500,pbr_observed:null,flow_reference:null},
 {code:'222222',name:'최근 상장 관찰',market:'KOSDAQ',
  price_history_status:'insufficient_history',published_price:null,
  reference_market_cap_eok:300,reference_trading_value_eok:1.7,pbr_observed:null},
 {code:'333333',name:'거래가 적은 관찰',market:'KOSPI',
  price_history_status:'price_source_or_validation_error',published_price:null,
  reference_market_cap_eok:500,reference_trading_value_eok:0,pbr_observed:null},
];
const catalog={schema:'v22-current-listed-catalog-0.1',date:'2026-10-19',
  listed_total:3,priced_count:1,status_counts:{priced:1,
    price_source_or_validation_error:1,insufficient_history:1,unattempted_batch:0},
  rows:catalogRows};
const merged=core.mergeCatalog(current,catalog);
assert.ok(merged&&merged.catalogLoaded,'full current-listing join must be valid');
const partialRow={
 code:'222222',status:'partial_technical_observed',bars:130,
 close_reference:13700,ma20:13100,ma60:12800,ma120_daily:12500,
 close_vs_ma20_pct:4.58,close_vs_ma60_pct:7.03,
 volume_ratio_20_vs_prior_20:1.3,obv_direction_60_daily:'rising',
 short_daily_ma_aligned:true,
};
const partial={
 schema:'v22-current-listed-partial-daily-60to159-v0.1',asof:'2026-10-19',
 listed_insufficient_history_count:1,
 partial_observed_count:1,still_unavailable_count:0,
 full_v22_score_allowed:false,v3_validated_technical_score_allowed:false,
 confirmed_buy_signal:false,raw_ohlcv_published:false,rows:[partialRow],
};
const together=core.mergePartial(merged,partial);
assert.ok(together&&together.partialLoaded);
assert.equal(together.partialObservedCount,1);
const young=together.stocks.find(x=>x.code==='222222');
assert.equal(young.price,null,'partial source-day close must not override official scored price row');
assert.equal(young.partialObservation.ma20,13100);
assert.equal(young.partialObservation.ma60,12800);
assert.equal(young.partialObservation.ma120_daily,12500);
assert.equal(core.makeRecord(young,'2026-10-19'),null,
 'partial stock cannot be paper-traded from an unvalidated price reference');
assert.equal(core.eligible(young,{kind:'partial',risk:'all',status:'missing',market:'KOSDAQ',search:''}),true);
assert.equal(core.eligible(together.stocks[0],{kind:'partial',risk:'all',status:'all',market:'all',search:''}),false);
assert.equal(core.eligible(young,{kind:'partial',risk:'all',status:'priced',market:'all',search:''}),false);
assert.equal(core.mergePartial(merged,{...partial,asof:'2026-10-16'}),null);
assert.equal(core.mergePartial(merged,{...partial,rows:[partialRow,partialRow]}),null);
assert.equal(core.mergePartial(merged,{...partial,partial_observed_count:0}),null);
assert.equal(core.mergePartial(merged,{...partial,raw_ohlcv_published:true}),null);
assert.equal(core.mergePartial(merged,{...partial,rows:[{...partialRow,bars:180}]}),null);
assert.equal(core.mergePartial(merged,{...partial,rows:[{...partialRow,
 stop_line:12000}]}),null);
assert.equal(core.mergePartial(merged,{...partial,rows:[{...partialRow,
 v3_buy_signal:true}]}),null);
assert.equal(core.mergePartial(merged,{...partial,rows:[{...partialRow,
 code:'333333'}]}),null,'volume-error cohort cannot become partial success');
assert.equal(core.mergePartial(merged,{...partial,rows:[{...partialRow,
 ma120_daily:null,bars:80}]}).partialObservedCount,1,
 '60-119 daily bars give only MA20/60, never synthetic daily 120');

const missing={...partial,partial_observed_count:0,still_unavailable_count:1,
 rows:[{code:'222222',status:'not_observable_partial',bars:35,
        unavailable_reason:'not_enough_60'}]};
const still=core.mergePartial(merged,missing);
assert.equal(still.partialObservedCount,0);
assert.equal(still.stocks.find(x=>x.code==='222222').price,null);
assert.equal(core.makeRecord(still.stocks.find(x=>x.code==='222222'),'2026-10-19'),null);
const html=fs.readFileSync(path.join(__dirname,'../docs/v3-manual.html'),'utf8');
assert.ok(html.includes('value="partial"'),'partial stock filter present');
const js=fs.readFileSync(path.join(__dirname,'../docs/v3-beta.js'),'utf8');
assert.ok(js.includes('v22-partial-history.json')&&js.includes('주봉120주선·정식 점수·매수 판단 없음'));
console.log('V2.2 short-history 60-159d optional join: offline no-order, no-score source tests passed');
