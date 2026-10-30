/* Anonymous historical OpenDART receipt progress; independent of daily scores. */
(function(root){
'use strict';
const SCHEMA='v3-public-dart-historical-receipt-progress-v1';
const int=x=>Number.isSafeInteger(x)&&x>=0;
const monthAt=n=>{
 const y=2016+Math.floor((9+n)/12),m=(9+n)%12+1;
 return String(y)+'-'+String(m).padStart(2,'0');
};
function validate(raw){
 if(!raw||raw.schema!==SCHEMA||
   raw.filing_months_total!==120||
   !int(raw.filing_months_checkpointed)||
   raw.filing_months_checkpointed>120||
   raw.filing_months_remaining!==120-raw.filing_months_checkpointed||
   typeof raw.filing_months_checkpointed_ratio!=='number'||
   !Number.isFinite(raw.filing_months_checkpointed_ratio)||
   Math.abs(raw.filing_months_checkpointed_ratio-
     raw.filing_months_checkpointed/120)>0.000002||
   !int(raw.private_receipt_pages_checkpointed)||
   raw.private_receipt_pages_checkpointed>2000000||
   raw.private_receipt_pages_checkpointed<
     raw.filing_months_checkpointed*4||
   !int(raw.private_filing_receipts_checkpointed)||
   raw.private_filing_receipts_checkpointed>
     raw.private_receipt_pages_checkpointed*100||
   !Array.isArray(raw.filing_category_scope)||
   raw.filing_category_scope.join(',')!=='B,C,E,I'||
   raw.last_checkpointed_filing_month!==
     (raw.filing_months_checkpointed?
      monthAt(raw.filing_months_checkpointed-1):null)||
   raw.current_collection_filing_month!==
     (raw.filing_months_checkpointed<120?
      monthAt(raw.filing_months_checkpointed):null)||
   raw.private_tail_digest_and_schema_verified!==
     (raw.private_receipt_pages_checkpointed>0)||
   raw.private_checkpoint_stable_during_read!==true||
   raw.full_historical_archive_independently_audited!==false||
   raw.corporate_actions_or_effective_dates_certified!==false||
   raw.historical_issuer_universe_pit_certified!==false||
   raw.historical_adjusted_prices_certified!==false||
   raw.v3_trade_scores_promoted!==false||
   raw.public_issuer_receipts_or_source_raw_published!==false||
   raw.provider_api_calls!==0)return null;
 return {
  completed:raw.filing_months_checkpointed,
  total:raw.filing_months_total,
  remaining:raw.filing_months_remaining,
  pages:raw.private_receipt_pages_checkpointed,
  receipts:raw.private_filing_receipts_checkpointed,
  last:raw.last_checkpointed_filing_month,
  current:raw.current_collection_filing_month,
  coverage:100*raw.filing_months_checkpointed/120
 };
}
if(typeof module!=='undefined'&&module.exports)module.exports={validate};
if(!root.document)return;
const D=root.document;
const names=['dartHistoryStatus','dartHistoryMonths','dartHistoryPages',
 'dartHistoryReceipts','dartHistoryNotes'];
const el=Object.fromEntries(names.map(id=>[id,D.getElementById(id)]));
if(Object.values(el).some(x=>!x))return;
const pretty=n=>n.toLocaleString('ko-KR');
async function load(){
 el.dartHistoryStatus.textContent='공시 원장 확인 중…';
 for(const key of ['dartHistoryMonths','dartHistoryPages','dartHistoryReceipts'])
  el[key].textContent='미확인';
 el.dartHistoryNotes.textContent='비공개 체크포인트의 비식별 요약을 검증 중입니다.';
 try{
  const response=await root.fetch('v3-dart-historical-receipt-progress.json',
                                  {cache:'no-store'});
  if(!response.ok)throw Error('missing_public_progress');
  const x=validate(await response.json());
  if(!x)throw Error('invalid_public_progress');
  el.dartHistoryStatus.textContent='수집 기간 '+x.coverage.toFixed(1)+'% · 연구용';
  el.dartHistoryMonths.textContent=pretty(x.completed)+' / '+pretty(x.total)+'개월';
  el.dartHistoryPages.textContent=pretty(x.pages)+'페이지';
  el.dartHistoryReceipts.textContent=pretty(x.receipts)+'건 · 접수 메타데이터';
  el.dartHistoryNotes.textContent='마지막 완료 '+(x.last||'없음')+
    ' · 현재 수집 '+(x.current||'완료')+
    ' · 남은 '+pretty(x.remaining)+'개월. B/C/E/I 분류의 비공개 공시 접수 메타데이터만 집계했으며 '+
    '전체 과거 공시, 기업행위 효력일, 수정주가, 상장폐지·당시 종목 구성 및 투자점수는 인증 전입니다.';
 }catch(_){
  el.dartHistoryStatus.textContent='수집원장 재검증 중';
  el.dartHistoryNotes.textContent='공시 진행률의 출처 또는 건수가 확인되지 않았습니다. '+
    '누락 기록을 0으로 표시하거나 투자점수에 반영하지 않습니다.';
 }
}
D.getElementById('reload')?.addEventListener('click',load);
load();
})(typeof window!=='undefined'?window:globalThis);
