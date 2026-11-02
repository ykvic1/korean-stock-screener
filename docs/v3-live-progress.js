/* Visible, source-bounded development progress. Research data are not certified scores. */
(function(root){
'use strict';
const AREA_WEIGHTS={volume:20,technical:25,financial:20,valuation:20,momentum:15};
const nonnegative=x=>Number.isSafeInteger(x)&&x>=0;
function validDate(x){
 if(typeof x!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(x))return false;
 const t=Date.parse(x+'T00:00:00Z');
 return Number.isFinite(t)&&new Date(t).toISOString().slice(0,10)===x;
}
function observedSummary(raw){
 if(!raw||raw.schema!=='v3-current-day-all-listed-observed-plus-dart-fcf-0.3'||
    !validDate(raw.asof)||!nonnegative(raw.listed_total)||
    raw.listed_total<100||raw.listed_total>6000||
    !nonnegative(raw.provisional_complete_100point_observation_count)||
    raw.provisional_complete_100point_observation_count>raw.listed_total||
    raw.certified_100point_count!==0||
    raw.not_an_investment_ranking!==true||
    raw.missing_weight_is_not_zero!==true||
    raw.independent_krx_adjusted_path_certification!==false||
    !raw.area_budgets||typeof raw.area_budgets!=='object'||
    Object.keys(raw.area_budgets).length!==5||
    Object.keys(AREA_WEIGHTS).some(k=>raw.area_budgets[k]!==AREA_WEIGHTS[k]))
    return null;
 return {asof:raw.asof,total:raw.listed_total,
  observed:raw.provisional_complete_100point_observation_count};
}
function dartSummary(raw){
 if(!raw||raw.schema!=='v3-public-dart-historical-receipt-progress-v1'||
    raw.filing_months_total!==120||
    !nonnegative(raw.filing_months_checkpointed)||
    raw.filing_months_checkpointed>120||
    raw.filing_months_remaining!==120-raw.filing_months_checkpointed||
    typeof raw.filing_months_checkpointed_ratio!=='number'||
    Math.abs(raw.filing_months_checkpointed_ratio-
             raw.filing_months_checkpointed/120)>0.000002||
    !Array.isArray(raw.filing_category_scope)||
    raw.filing_category_scope.join(',')!=='B,C,E,I'||
    raw.private_tail_digest_and_schema_verified!==true||
    raw.private_checkpoint_stable_during_read!==true||
    raw.public_issuer_receipts_or_source_raw_published!==false||
    raw.provider_api_calls!==0||
    raw.full_historical_archive_independently_audited!==false||
    raw.historical_adjusted_prices_certified!==false||
    raw.historical_issuer_universe_pit_certified!==false||
    raw.corporate_actions_or_effective_dates_certified!==false||
    raw.v3_trade_scores_promoted!==false||
    !nonnegative(raw.private_receipt_pages_checkpointed)||
    !nonnegative(raw.private_filing_receipts_checkpointed))
    return null;
 return {complete:raw.filing_months_checkpointed,total:120,
  remaining:raw.filing_months_remaining};
}
if(typeof module!=='undefined'&&module.exports)
 module.exports={observedSummary,dartSummary};
if(!root.document)return;
const doc=root.document;
const fields={
 date:doc.getElementById('liveProgressAsOf'),
 coverage:doc.getElementById('liveProgressCoverage'),
 dart:doc.getElementById('liveProgressDart'),
 note:doc.getElementById('liveProgressNotice')
};
if(Object.values(fields).some(x=>!x))return;
const fmt=x=>x.toLocaleString('ko-KR');
let generation=0;
async function getJson(path){
 const response=await root.fetch(path,{cache:'no-store'});
 if(!response.ok)throw Error('source_unavailable');
 return response.json();
}
async function load(){
 const run=++generation;
 fields.date.textContent='확인 중';
 fields.coverage.textContent='확인 중';
 fields.dart.textContent='확인 중';
 fields.note.textContent='각 데이터 원천의 기준일과 수집 상태를 확인 중입니다.';
 const [market,filings]=await Promise.allSettled([
  getJson('v3-current-observed-all-listed-summary.json'),
  getJson('v3-dart-historical-receipt-progress.json')
 ]);
 if(run!==generation)return;
 const m=market.status==='fulfilled'?observedSummary(market.value):null;
 const f=filings.status==='fulfilled'?dartSummary(filings.value):null;
 fields.date.textContent=m?m.asof:'재확인 필요';
 fields.coverage.textContent=m?
  fmt(m.observed)+' / '+fmt(m.total)+'종목':'재확인 필요';
 fields.dart.textContent=f?
  fmt(f.complete)+' / '+fmt(f.total)+'개월':'재확인 필요';
 fields.note.textContent=(m||f?
  '각 원천의 마지막 저장 결과입니다. ':'원천 자료 확인 대기 중입니다. ')+
  '100점은 계산 가능한 배점의 관측 범위이며 정식 투자점수나 수익 전망이 아닙니다. '+
  '과거 공시 접수 수집도 수정주가·기업행위·PIT 인증과 별개입니다.';
}
doc.getElementById('reload')?.addEventListener('click',load);
load();
})(typeof window!=='undefined'?window:globalThis);
