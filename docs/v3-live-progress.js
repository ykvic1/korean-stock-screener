/* Visible, source-bounded development progress. Research data are not certified scores. */
(function(root){
'use strict';
const AREA_WEIGHTS={volume:20,technical:25,financial:20,valuation:20,momentum:15};
const R1D_WEIGHTS={volume:25,technical:30,financial:12.5,valuation:12.5,momentum:20};
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
function operationalSummary(raw){
 const c=raw?.operational_coverage;
 if(!raw||!['v3-stable-r1d-operational-research','v3-stable-r1e-operational-research'].includes(raw.version)||
    raw.performance_validated!==false||!validDate(raw.asof)||
    !c||c.status!=='provisional_operational'||
    !/^[a-f0-9]{64}$/.test(c.source_digest||'')||
    c.provider_calls!==0||c.missing_is_not_zero!==true||
    c.investment_score_certified!==false||c.performance_validated!==false||
    !nonnegative(c.listed_total)||c.listed_total<100||c.listed_total>6000||
    !nonnegative(c.ranked_count)||!nonnegative(c.available_100_count)||
    !nonnegative(c.available_95_count)||!nonnegative(c.available_90_count)||
    c.available_100_count>c.available_95_count||
    c.available_95_count>c.available_90_count||
    c.available_90_count>c.ranked_count||c.ranked_count>c.listed_total||
    !c.budgets||Object.keys(c.budgets).length!==5||
    Object.keys(R1D_WEIGHTS).some(k=>c.budgets[k]!==R1D_WEIGHTS[k]))return null;
 return {asof:raw.asof,model:raw.version.includes('r1e')?'R1e':'R1d',total:c.listed_total,observed:c.available_100_count,
  near95:c.available_95_count,near90:c.available_90_count,ranked:c.ranked_count};
}
// A new market close can arrive before the annual DART replay. Explain the
// temporary drop without blending previous-day finance into today's 100pts.
function scoreRolloverNotice(raw,previous){
 const current=observedSummary(raw);
 if(!current||!raw.observed_issuer_counts||
    raw.observed_issuer_counts.financial!==0||
    raw.observed_issuer_counts.valuation!==0||
    raw.factor_unobserved_weight_gap?.financial!==20||
    raw.factor_unobserved_weight_gap?.valuation!==20||
    typeof raw.fiscal_reference_join_status!=='string'||
    !['not_ready_or_unverified_annual_source','unavailable_or_not_same_source_day',
      'unavailable_or_legacy_fiscal_component_contract'].includes(
       raw.fiscal_reference_join_status))return '';
 const base='재무·밸류 40점은 '+current.asof+
   ' 시장일과 원천 동기화·검증 대기로 미확보입니다. '+
   '따라서 개인용 100점 환산은 보류되며, 미확보는 0점이 아닙니다.';
 if(!previous||previous.schema!==
      'v3-real-saved-source-six-pillar-coverage-0.1'||
    previous.investment_grade_scores_confirmed!==false||
    !validDate(previous.market_asof)||
    previous.market_asof>=current.asof||
    Date.parse(current.asof+'T00:00:00Z')-
     Date.parse(previous.market_asof+'T00:00:00Z')>10*86400000||
    !nonnegative(previous.listed_total)||
    previous.listed_total<100||previous.listed_total>6000||
    !previous.six_pillar_numeric_provisional_issuer_counts)return base;
 const old=previous.six_pillar_numeric_provisional_issuer_counts;
 if(!nonnegative(old.financial)||!nonnegative(old.valuation)||
    old.financial>previous.listed_total||
    old.valuation>previous.listed_total)return base;
 return '이전 '+previous.market_asof+' 참고자료에서는 재무 '+
   old.financial.toLocaleString('ko-KR')+'개·밸류 '+
   old.valuation.toLocaleString('ko-KR')+
   '개 종목의 잠정 관측이 있었습니다. 과거 종목구성·기준일이 달라 '+
   '오늘 점수에 합산하지 않습니다. '+base;
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
function closeAuditSummary(raw,marketAsOf){
 const c=raw?.counts?.saved_market;
 if(!raw||raw.schema!=='v3-saved-close-official-krx-diagnostic-v1'||
    !validDate(raw.asof)||!c||!nonnegative(c.requested)||c.requested<1||
    !nonnegative(c.exact_close)||!nonnegative(c.different_close)||
    !nonnegative(c.absent_in_krx)||!nonnegative(c.invalid_krx_price_row)||
    !nonnegative(c.unusable_saved_close)||
    c.exact_close+c.different_close+c.absent_in_krx+
    c.invalid_krx_price_row+c.unusable_saved_close!==c.requested||
    raw.source_requests!==2||raw.investment_score_certified!==false||
    raw.krx_regular_only_volume_verified!==false||
    raw.nxt_and_after_hours_exclusion_verified!==false||
    raw.changed_market_price_rows!==0||raw.added_available_weight!==0)
    return null;
 const fresh=validDate(marketAsOf)&&marketAsOf===raw.asof;
 return {asof:raw.asof,checked:c.requested,different:c.different_close,
  fresh,percent:Math.round(c.different_close/c.requested*1000)/10};
}
if(typeof module!=='undefined'&&module.exports)
 module.exports={observedSummary,operationalSummary,dartSummary,scoreRolloverNotice,closeAuditSummary};
if(!root.document)return;
const doc=root.document;
const fields={
 date:doc.getElementById('liveProgressAsOf'),
 coverage90:doc.getElementById('liveProgressCoverage90'),
 coverage:doc.getElementById('liveProgressCoverage'),
 dart:doc.getElementById('liveProgressDart'),
 note:doc.getElementById('liveProgressNotice'),
 close:doc.getElementById('krxCloseAlert')
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
 fields.coverage90.textContent='확인 중';
 fields.coverage.textContent='확인 중';
 fields.dart.textContent='확인 중';
 fields.note.textContent='각 데이터 원천의 기준일과 수집 상태를 확인 중입니다.';
 fields.close.firstChild.textContent='가격 원천을 공식 KRX와 대조 중입니다. ';
 const [market,filings,previous,operational,closeAudit]=await Promise.allSettled([
  getJson('v3-current-observed-all-listed-summary.json'),
  getJson('v3-dart-historical-receipt-progress.json'),
  getJson('v3-real-source-six-area-coverage.json'),
  getJson('v3-operational-research-comparison.json'),
  getJson('v3-krx-saved-close-crosscheck.json')
 ]);
 if(run!==generation)return;
 const m=market.status==='fulfilled'?observedSummary(market.value):null;
 const f=filings.status==='fulfilled'?dartSummary(filings.value):null;
 const proposed=operational.status==='fulfilled'?operationalSummary(operational.value):null;
 const o=proposed&&(!m||(proposed.total===m.total&&proposed.asof<=m.asof))?proposed:null;
 const price=closeAudit.status==='fulfilled'?closeAuditSummary(
  closeAudit.value,o?.asof||m?.asof):null;
 fields.close.firstChild.textContent=price?
  price.fresh?
   '가격 원천 불일치 · '+price.asof+' 공식 KRX 일별 종가와 저장 종가가 '+
   fmt(price.different)+' / '+fmt(price.checked)+'종목 ('+price.percent+'%)에서 다릅니다. '+
   '네이버 FChart 일봉의 애프터마켓 최종가 혼입이 실제 확인됐습니다. 정규장 전용 OHLCV·수정주가 미인증 · 운영점수는 잠정 참고용입니다. ':
   '최근 KRX 종가 대조 '+price.asof+' · '+fmt(price.different)+' / '+fmt(price.checked)+
   '종목 불일치. 현재 '+(o?.asof||m?.asof||'시장')+' 자료에 대한 새 대조는 대기 중입니다. '+
   '기존 FChart 일봉의 애프터마켓 혼입 위험이 있어 정규장 가격·거래량 근거는 아직 미인증입니다. ':
  'KRX 정규장 종가와 저장 일봉의 독립 대조 결과를 확인할 수 없습니다. '+
  '원천·거래세션 인증 전이므로 점수는 잠정 참고용입니다. ';
 fields.date.textContent=o?o.asof+' · '+o.model+' 점수':m?m.asof:'재확인 필요';
 fields.coverage90.textContent=o?
  fmt(o.near90)+' / '+fmt(o.total)+'종목 ('+o.model+')': '신규 모델 집계 대기';
 fields.coverage.textContent=o?
  fmt(o.observed)+' / '+fmt(o.total)+'종목 ('+o.model+')':m?
  fmt(m.observed)+' / '+fmt(m.total)+'종목 (기존 모델)':'재확인 필요';
 fields.dart.textContent=f?
  fmt(f.complete)+' / '+fmt(f.total)+'개월':'재확인 필요';
 const rollover=!o&&market.status==='fulfilled'?scoreRolloverNotice(
  market.value,previous.status==='fulfilled'?previous.value:null):'';
 fields.note.textContent=(rollover?rollover+' ':'')+
  (o&&m&&o.asof<m.asof?'신규 운영점수는 '+o.asof+' 기준으로 유지 중이며 최신 종가의 재계산을 기다립니다. ':'')+
  (m||f||o?
  '각 원천의 마지막 저장 결과입니다. ':'원천 자료 확인 대기 중입니다. ')+
  (o?o.model+' 90점 이상·100점은 전체 상장종목에서 새 배점(25/30/12.5/12.5/20)의 실제 관측 가능 배점 기준 종목 수입니다. '+(o.model==='R1e'?'손절 참고선은 가감점·정렬에 반영하지 않습니다. ':'R1d는 과거 손절 가감점이 포함된 산식으로 보존됩니다. ')+'거래정지·이력 부족 종목도 전체 분모에 남으며, 미확보분을 0점으로 계산하지 않습니다. ':
   '아직 신규 모델 집계가 연결되지 않아 기존 모델 배점의 확보율만 표시 중입니다. ')+
  '확보배점은 획득점수·정식 투자점수·예상 수익률이 아닙니다. '+
  '과거 공시 접수 수집도 수정주가·기업행위·PIT 인증과 별개입니다.';
}
doc.getElementById('reload')?.addEventListener('click',load);
load();
})(typeof window!=='undefined'?window:globalThis);
