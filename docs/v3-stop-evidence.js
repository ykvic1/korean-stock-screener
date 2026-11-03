/* Source-dated reference checks only. No score writes or risk forecasts. */
(function(root){
 'use strict';
 const finite=n=>typeof n==='number'&&Number.isFinite(n);
 const day=s=>typeof s==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&
  Number.isFinite(Date.parse(s+'T00:00:00Z'))&&
  new Date(Date.parse(s+'T00:00:00Z')).toISOString().slice(0,10)===s;
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function profile(row,marketDay){
  const r=row&&typeof row==='object'?row:{};
  const asof=day(r.price_date)?r.price_date:null;
  const price=finite(r.price)&&r.price>0?r.price:null;
  const stop=finite(r.stop_line)&&r.stop_line>0?r.stop_line:null;
  const below=price!==null&&stop!==null&&stop<price;
  const distance=below?(price-stop)/price*100:null;
  // Match the existing stability overlay's two percentage-point tolerance.
  // Consistency is an arithmetic reference check, not source certification.
  const consistent=distance!==null&&finite(r.stop_pct)&&r.stop_pct<0&&
   Math.abs(distance+r.stop_pct)<=2;
  const high=finite(r.reference_resistance)&&r.reference_resistance>0?
   r.reference_resistance:null;
  const rawRoom=price!==null&&high!==null&&high>price?(high-price)/price*100:null;
  const room=finite(rawRoom)?rawRoom:null;
  const rawRewardRisk=consistent&&room!==null?room/distance:null;
  const freshness=!asof||!day(marketDay)?'unknown':
   asof>marketDay?'future':asof===marketDay?'same_day':'prior';
  const reasons=[];
  if(price===null)reasons.push('missing_price');
  if(!below)reasons.push('missing_or_nonprotective_stop_reference');
  else if(!consistent)reasons.push('missing_or_inconsistent_stop_distance');
  if(room===null)reasons.push('no_above_price_historical_high_reference');
  if(freshness!=='same_day')reasons.push('not_same_day_reference');
  return {
   asof,marketDay:day(marketDay)?marketDay:null,freshness,
   price,stop,stopBelowPrice:below,stopDistancePct:distance,
   reportedDistanceConsistent:consistent,historicalHigh:high,
   historicalHighRoomPct:room,
   referenceRewardRisk:finite(rawRewardRisk)?rawRewardRisk:null,
   sameDayReferenceReady:reasons.length===0,
   missingReferenceReasons:reasons,
   verifiedHistoricalSampleCount:null,historicalStopFirstPct:null,
   frozenSetupBacktestLinked:false,regularSessionVerified:false,
   corporateActionsVerified:false,executionVerified:false,
   scoringAllowed:false,
  };
 }
 function markup(row,marketDay){
  const p=profile(row,marketDay);
  const pct=n=>n===null?'미확인':n.toFixed(1)+'%';
  const won=n=>n===null?'미확인':n.toLocaleString('ko-KR',{
   maximumFractionDigits:2,notation:n>=1e12?'scientific':'standard'})+'원';
  const cell=(label,value,note)=>'<div><small>'+esc(label)+'</small><strong>'+esc(value)+
   '</strong><small>'+esc(note)+'</small></div>';
  const freshness={same_day:'시장자료와 기준일 일치',prior:'이전 관측 유지',
   future:'미래 기준일 · 대조 필요',unknown:'기준일 확인 필요'}[p.freshness];
  return '<details class="u-stop-evidence" aria-label="손절 위험 근거 점검">'+
   '<summary>손절 위험 근거 · 자료 점검</summary>'+
   '<p class="u-muted">가격자료 '+esc(p.asof||'미확인')+' · '+esc(freshness)+'</p>'+
   '<div class="u-detail-grid">'+
   cell('구조 무효화 참고선',won(p.stop),p.stopBelowPrice?'관측 가격 아래 참고선':'손절 기준으로 사용 전 확인 필요')+
   cell('참고 손절거리',pct(p.stopDistancePct),p.reportedDistanceConsistent?
    '표시 거리와 약식 대조 일치':'표시 거리 근거 확인 필요')+
   cell('과거 고점까지 참고거리',pct(p.historicalHighRoomPct),'과거 관측 고점 기준')+
   cell('참고 손익비',p.referenceRewardRisk===null?'미확인':p.referenceRewardRisk.toFixed(2)+'배',
    '고점까지 참고거리 ÷ 손절거리')+
   cell('과거 손절률','검증 표본 미연결','신호 당시 고정한 조건으로 검증 필요')+
   cell('시가 갭·체결 위험','검증 전','손절선보다 불리한 청산 가능')+'</div>'+
   '<p class="u-muted">참고선의 계산·자료 상태를 점검합니다. 과거 손절·목표가 선도달률, '+
   '정규장 출처와 기업행위는 독립 검증 전이며 미래 손절 확률은 미산출입니다.</p></details>';
 }
 const api=Object.freeze({profile,markup});
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 root.V3StopEvidence=api;
})(typeof window!=='undefined'?window:globalThis);
