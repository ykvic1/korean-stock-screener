/* Display-only zero-volume status. Does not alter price, score or universe. */
(function(root){
'use strict';
const SCHEMA='v3-trading-status-display-v1';
const DAY=/^\d{4}-\d{2}-\d{2}$/;
const CODE=/^\d{6}$/;
const STATES=new Set(['zero_volume','trade_observed','volume_unknown']);
const DURATIONS=new Set(['short_1_to_2','extended_3_plus','unknown']);
function isDay(day){
 return typeof day==='string'&&DAY.test(day)&&
  !Number.isNaN(Date.parse(day+'T00:00:00Z'))&&
  new Date(day+'T00:00:00Z').toISOString().slice(0,10)===day;
}
function attach(model,report){
 if(!model||!Array.isArray(model.rows)||!isDay(model.date)||
    !Number.isInteger(model.marketEligible)||model.rows.length!==model.marketEligible||
    !report||report.schema!==SCHEMA||report.asof!==model.date||
    report.listed_total!==model.marketEligible||
    report.krx_regular_session_verified!==false||report.score_points_added!==0||
    report.default_state!=='trade_observed'||
    !report.rows||typeof report.rows!=='object'||Array.isArray(report.rows)||
    Object.keys(report.rows).length!==(report.counts?.zero_volume??-1)+(report.counts?.volume_unknown??-1))return null;
 const seen=new Set(),updates=[];
 const counts={zero_volume:0,trade_observed:0,volume_unknown:0,
  short_halt_1_to_2_observed_sessions:0,extended_halt_3_plus_observed_sessions:0,
  unknown_halt_duration:0};
 for(const item of model.rows){
  const r=item?.r,code=r?.code,explicit=report.rows[code];
  const v=explicit||{state:'trade_observed',duration:null,observed_zero_streak:null,
    reason:null,official_reason_verified:false};
  if(typeof code!=='string'||!CODE.test(code)||seen.has(code)||
     (explicit&&(v.market!==r.market||v.name!==r.name))||!STATES.has(v.state)||
     v.official_reason_verified!==false||v.reason!==null||
     (explicit&&v.state==='trade_observed'))return null;
  seen.add(code);counts[v.state]++;
  if(v.state==='zero_volume'){
   if(!DURATIONS.has(v.duration)||
     !(v.observed_zero_streak===null||Number.isInteger(v.observed_zero_streak)&&v.observed_zero_streak>=1)||
     (v.duration==='short_1_to_2'&&!(v.observed_zero_streak>=1&&v.observed_zero_streak<=2))||
     (v.duration==='extended_3_plus'&&!(v.observed_zero_streak>=3))||
     (v.duration==='unknown'&&v.observed_zero_streak!==null))return null;
   counts[{short_1_to_2:'short_halt_1_to_2_observed_sessions',
    extended_3_plus:'extended_halt_3_plus_observed_sessions',
    unknown:'unknown_halt_duration'}[v.duration]]++;
  }else if(v.duration!==null||v.observed_zero_streak!==null)return null;
  updates.push([r,v]);
 }
 for(const [key,value] of Object.entries(counts))if(report.counts?.[key]!==value)return null;
 for(const [r,v] of updates)r.v3_trading_status={...v,asof:report.asof};
 return {linked:updates.length,halted:counts.zero_volume,
  short:counts.short_halt_1_to_2_observed_sessions,
  extended:counts.extended_halt_3_plus_observed_sessions,
  durationUnknown:counts.unknown_halt_duration};
}
function label(r){
 const v=r?.v3_trading_status;
 if(v?.state!=='zero_volume')return null;
 if(v.duration==='short_1_to_2')return '단기 거래정지 · '+v.observed_zero_streak+'거래일 관측';
 if(v.duration==='extended_3_plus')return '거래정지 · '+v.observed_zero_streak+'거래일 연속 관측';
 return '거래정지 · 기간 미확인';
}
function explanation(r){
 if(!label(r))return null;
 return '상장목록 기준 거래량 0 · 정지 사유 미확인 · KRX 정규장 독립검증 전. '+
  '단기 표시는 정상 거래량 관측에서 0으로 전환된 이후 연속 조회된 경우만 적용합니다.';
}
const core={attach,label,explanation};
if(typeof module!=='undefined'&&module.exports)module.exports=core;
root.V3TradingStatus=core;
})(typeof window!=='undefined'?window:globalThis);
