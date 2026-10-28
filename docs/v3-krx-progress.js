/* Current PRIVATE KRX transformed-source progress; public aggregate only. */
(function(root){
'use strict';
const SCHEMA='v3-verified-private-derived-krx-historical-progress-0.1';
const isInt=x=>Number.isSafeInteger(x)&&x>=0;
const date=x=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&
  !Number.isNaN(Date.parse(x+'T00:00:00Z'));
function validate(raw){
 if(!raw||raw.schema!==SCHEMA||
   !date(raw.source_candidate_start)||!date(raw.source_candidate_end)||
   raw.source_candidate_start>=raw.source_candidate_end||
   !isInt(raw.five_session_cohorts_target)||
   raw.five_session_cohorts_target<100||raw.five_session_cohorts_target>1500||
   !isInt(raw.five_session_cohorts_completed)||
   raw.five_session_cohorts_completed>raw.five_session_cohorts_target||
   raw.five_session_cohorts_remaining!==raw.five_session_cohorts_target-
     raw.five_session_cohorts_completed||
   typeof raw.source_history_coverage_ratio!=='number'||
   Math.abs(raw.source_history_coverage_ratio-
     raw.five_session_cohorts_completed/raw.five_session_cohorts_target)>0.000002||
   raw.private_immutable_evidence_tails_verified!==true||
   raw.private_repo_verified_before_read!==true||
   raw.historical_delisted_universe_pit_verified!==false||
   raw.corporate_actions_or_adjusted_prices_verified!==false||
   raw.official_exchange_historical_factor_certified!==false||
   raw.industry_15_or_other_v3_trade_score_promoted!==false||
   raw.public_issuer_prices_or_individual_returns_written!==false||
   raw.provider_data_new_calls!==0)return null;
 const total=raw.five_session_cohorts_completed;
 if(!isInt(raw.cross_cohort_bridges_completed)||
   !isInt(raw.cross_cohort_bridges_required_for_completed_source)||
   raw.cross_cohort_bridges_required_for_completed_source!==Math.max(0,total-1)||
   raw.cross_cohort_bridges_completed>Math.max(0,total-1)||
   raw.cross_cohort_bridges_backlog!==
     Math.max(0,total-1)-raw.cross_cohort_bridges_completed)return null;
 const verified=Math.min(total,raw.cross_cohort_bridges_completed+1);
 const r=raw.rolling_return_horizon_research;
 if(!r||typeof r!=='object'||Object.keys(r).sort().join(',')!=='120,20,250,60')return null;
 const windows={};
 for(const h of [20,60,120,250]){
  const x=r[String(h)],max=Math.max(0,verified-h/5+1);
  if(!x||!isInt(x.complete_cohort_windows)||
    !isInt(x.possible_windows_from_current_verified_bridges)||
    !isInt(x.backlog_windows_at_this_snapshot)||
    x.possible_windows_from_current_verified_bridges!==max||
    x.complete_cohort_windows>max||
    x.backlog_windows_at_this_snapshot!==max-x.complete_cohort_windows||
    x.unadjusted_price_return_approved!==false)return null;
  windows[h]=x.complete_cohort_windows;
 }
 if(!isInt(raw.price_discontinuity_heuristic_cohorts_reviewed)||
    raw.price_discontinuity_heuristic_cohorts_reviewed>verified||
    raw.price_discontinuity_review_backlog_at_verified_bridges!==
      verified-raw.price_discontinuity_heuristic_cohorts_reviewed)return null;
 const last=raw.last_completed_source_session;
 if(total===0?last!==null:!date(last)||last>raw.source_candidate_end||
                                 last<raw.source_candidate_start)return null;
 return {
  historyStart:raw.source_candidate_start,historyEnd:raw.source_candidate_end,
  completed:total,target:raw.five_session_cohorts_target,
  remaining:raw.five_session_cohorts_remaining,
  bridges:raw.cross_cohort_bridges_completed,
  bridgesTarget:raw.cross_cohort_bridges_required_for_completed_source,
  risk:raw.price_discontinuity_heuristic_cohorts_reviewed,
  riskTarget:verified,windows,last,
  coverage:100*raw.source_history_coverage_ratio
 };
}
if(typeof module!=='undefined'&&module.exports)module.exports={validate};
if(!root.document)return;
const D=root.document;
const names=['krxProgressStatus','krxHistoryCollected','krxHistoryBridges',
  'krxHistoryRolling','krxHistoryRisk','krxHistoryNotes'];
const el=Object.fromEntries(names.map(id=>[id,D.getElementById(id)]));
if(Object.values(el).some(x=>!x))return;
const pretty=n=>n.toLocaleString('ko-KR');
async function load(){
 el.krxProgressStatus.textContent='비공개 파생자료 검사 중';
 for(const key of ['krxHistoryCollected','krxHistoryBridges',
                   'krxHistoryRolling','krxHistoryRisk'])
   el[key].textContent='미확인';
 el.krxHistoryNotes.textContent='공식 KRX 10년 과거 자료를 점검하고 있습니다.';
 try{
  const response=await root.fetch('v3-krx-derived-backfill-progress.json?v='+Date.now(),
                                  {cache:'no-store'});
  if(!response.ok)throw Error('no_checkpoint');
  const o=validate(await response.json());
  if(!o)throw Error('invalid_checkpoint');
  el.krxProgressStatus.textContent='고정 과거구간 5일 단위 '+o.coverage.toFixed(1)+'% 확보';
  el.krxHistoryCollected.textContent=pretty(o.completed)+' / '+pretty(o.target);
  el.krxHistoryBridges.textContent=pretty(o.bridges)+' / '+pretty(o.bridgesTarget);
  el.krxHistoryRolling.textContent=[20,60,120,250].map(h=>h+'일 '+o.windows[h]).join(' · ');
  el.krxHistoryRisk.textContent=pretty(o.risk)+' / '+pretty(o.riskTarget);
  el.krxHistoryNotes.textContent='집계 범위 '+o.historyStart+'~'+o.historyEnd+
    ' (종료일 이후 거래일은 완료율에 미포함) · 과거 자료 잔여 '+pretty(o.remaining)+
    '개(5거래일 묶음) · 마지막 원천 관측 '+(o.last||'미확인')+
    '. 비공개 저장 파생지표 진행률이며, 과거 상장폐지 종목·수정주가와 '+
    '정식 V3 100점 검증은 포함하지 않습니다.';
 }catch(_){
  el.krxProgressStatus.textContent='자료 재검증 대기';
  el.krxHistoryNotes.textContent='비공개 원천 확인 또는 공개 진행 요약의 검증이 끝나지 않았습니다. '+
    '과거 완료율을 임의로 계산하지 않습니다.';
 }
}
D.getElementById('reload')?.addEventListener('click',load);
load();
})(typeof window!=='undefined'?window:globalThis);
