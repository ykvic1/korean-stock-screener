(function(root){
'use strict';
const VERSION='v3-stable-r1d-operational-research';
const ACCEPTED_VERSIONS=new Set([VERSION,'v3-stable-r1c-operational-research','v3-stable-r1b-operational-research','v3-stable-r1a-operational-research']);
const BUDGET={volume:25,technical:30,financial:12.5,valuation:12.5,momentum:20};
const MAXES={volume:{long_horizon_growth:6,persistence:5,concentration_quality:2,up_down_asymmetry:2,post_spike_absorption:3,correction_contraction:1,weekly_structure_increment:2,obv_price_divergence:4},technical:{daily_pattern:8.4,weekly_pattern:9.6,ma20_ma60_cross_near_ma240:6,daily_rsi:1.2,weekly_rsi:1.8,long_ma_position:1.8,independent_breakout_confirmation:1.2},financial:{revenue_growth:3.5,operating_growth_margin:3,cashflow_fcf_persistence:3,balance_sheet_strength:3},valuation:{per_eps_quality_growth:5,pbr_equity_quality:2.5,fcf_yield:2.5,sales_to_cap:2.5},momentum:{existing_momentum:15,daily_ma_slope_turn:2,weekly_ma_slope_turn:3}};
const isDay=d=>typeof d==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(d)&&!Number.isNaN(Date.parse(d+'T00:00:00Z'))&&new Date(d+'T00:00:00Z').toISOString().slice(0,10)===d;
const LABELS={volume:'거래량·거래대금',technical:'기술',financial:'재무',valuation:'가치',momentum:'모멘텀'};
const COMPONENTS={long_horizon_growth:'다중기간 거래대금 증가',persistence:'거래 지속성',concentration_quality:'거래 집중도',up_down_asymmetry:'상승·하락 거래 비대칭',post_spike_absorption:'매물 소화',correction_contraction:'조정 거래 감소',weekly_structure_increment:'주봉 거래 추가 확인',obv_price_divergence:'OBV·가격 괴리',daily_pattern:'일봉 패턴',weekly_pattern:'주봉 패턴',ma20_ma60_cross_near_ma240:'20·60MA 교차/240MA 조건',daily_rsi:'일봉 RSI',weekly_rsi:'주봉 RSI',long_ma_position:'장기 MA 위치',independent_breakout_confirmation:'독립 돌파 확인',revenue_growth:'매출 성장',operating_growth_margin:'영업 수익성(기존 관측)',cashflow_fcf_persistence:'현금흐름·FCF',balance_sheet_strength:'재무건전성',per_eps_quality_growth:'PER·EPS',pbr_equity_quality:'PBR',fcf_yield:'FCF 수익률',sales_to_cap:'매출/시총',existing_momentum:'기존 모멘텀',daily_ma_slope_turn:'일봉 MA 전환',weekly_ma_slope_turn:'주봉 MA 전환'};
const finite=n=>typeof n==='number'&&Number.isFinite(n);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const near=(a,b)=>finite(a)&&finite(b)&&Math.abs(a-b)<.00002;
const round=n=>Math.round((n+Number.EPSILON)*10)/10;
function validRow(r,day){
 if(!r||typeof r.name!=='string'||!['KOSPI','KOSDAQ'].includes(r.market)||
    !finite(r.available)||r.available<0||r.available>100||!r.areas||!r.parts||
    !['unknown','partial'].includes(r.risk_status)||!Array.isArray(r.risk_warnings)||
    !finite(r.capital_deduction)||r.capital_deduction<0||r.capital_deduction>5||
    !finite(r.stop_adjustment)||r.stop_adjustment< -3||r.stop_adjustment>2||
    !finite(r.risk_deduction)||r.risk_deduction<0||r.risk_deduction>5||
    !isDay(r.asof)||r.asof>day)return false;
 let points=0,available=0;
 for(const [a,max] of Object.entries(BUDGET)){
  const area=r.areas[a],parts=r.parts[a];if(!area||!parts)return false;
  let p=0,w=0;
  for(const [key,v] of Object.entries(parts)){
   if(!Object.hasOwn(MAXES[a],key)||!Array.isArray(v)||v.length!==3||
     !finite(v[0])||!finite(v[1])||v[0]<0||v[1]<=0||v[0]>v[1]+.000001||v[1]>MAXES[a][key]+.000001||
     !isDay(v[2])||v[2]>day)return false;
   p+=v[0];w+=v[1];
  }
  if(!near(p,area.points)||!near(w,area.available)||w>max+.000001)return false;
  points+=p;available+=w;
 }
 if(!near(available,r.available))return false;
 if(!available)return r.score===null&&r.base===null;
 return near(points,r.base)&&near(r.score,round(Math.max(0,Math.min(100,r.base-r.capital_deduction+r.stop_adjustment-r.risk_deduction))));
}
function attach(model,report){
 if(!model||!Array.isArray(model.rows)||!report||!ACCEPTED_VERSIONS.has(report.version)||
    report.format!=='compact-parts-v1'||report.status!=='provisional_operational'||
    !isDay(report.asof)||report.asof>model.date||report.investment_score_certified!==false||report.performance_validated!==false||
    report.missing_is_not_zero!==true||report.unobserved_weight_not_normalized!==true||
    report.provider_calls!==0||!Number.isInteger(report.listed_total)||report.listed_total<1||report.listed_total>10000||!report.rows||Object.keys(report.rows).some(k=>!/^\d{6}$/.test(k))||Object.keys(report.rows).length!==report.listed_total||
    Object.entries(BUDGET).some(([k,v])=>report.budgets?.[k]!==v)||
    Object.values(report.rows).some(r=>!validRow(r,report.asof)))return 0;
 // A new listing or a renamed/reclassified issuer must never blank the
 // entire market. Quarantine disputed identities, never assign by code alone.
 let n=0;
 for(const {r} of model.rows){
  const v=report.rows[r.code];if(!v){
   delete r.v3_operational;
   delete r.v3_operational_version;
   r.v3_operational_evidence_missing=true;
   continue;
  }
  delete r.v3_operational_evidence_missing;
  if(v.name!==r.name||v.market!==r.market){
   delete r.v3_operational;
   delete r.v3_operational_version;
   r.v3_operational_identity_blocked=true;
   continue;
  }
  delete r.v3_operational_identity_blocked;
  r.v3_operational=v;r.v3_operational_version=report.version;n++;
 }
 model.operationalVersion=report.version;return n;
}
// The price snapshot intentionally omits suspended/newly listed/unpriced
// issuers. Append them only if today's source date, roster size and every
// currently priced code agree. On a roster rollover, preserve per-issuer
// dated scores without presenting old delistings as today's active listings.
function attachAllListed(model,report){
 if(!model||!Array.isArray(model.rows)||!Number.isInteger(model.marketEligible)||
    model.marketEligible<1||model.marketEligible>10000||
    model.rows.length>model.marketEligible)return {linked:0,unpriced:0,blocked:0,missing:0,stale:false};
 const seen=new Set();
 for(const item of model.rows){
  const code=item?.r?.code;
  if(typeof code!=='string'||!/^[0-9]{6}$/.test(code)||seen.has(code))
   return {linked:0,unpriced:0,blocked:0,missing:0,stale:false};
  seen.add(code);
 }
 const attached=attach(model,report);
 const blocked=model.rows.filter(x=>x.r.v3_operational_identity_blocked).length;
 const missing=model.rows.filter(x=>x.r.v3_operational_evidence_missing).length;
 if(attached+blocked+missing!==seen.size||!model.operationalVersion)
  return {linked:0,unpriced:0,blocked:0,missing:0,stale:false};
 let unpriced=0;
 const sameRoster=!missing&&model.date===report.asof&&model.marketEligible===report.listed_total;
 if(sameRoster){
  for(const [code,v] of Object.entries(report.rows)){
   if(seen.has(code))continue;
   model.rows.push({r:{code,name:v.name,market:v.market,price:null,
     price_date:null,score:null,coverage_pct:null,components:null,
     unpriced_operational_only:true,v3_operational:v,
     v3_operational_version:report.version},q:null,rec:null,
     watch:null,quant:false,researchCandidate:false,qualified:false,
     caution:false,risk:null,highRisk:true,financialRef:null,
     rollingBook:null,fourYearRef:null,decision:null});
   unpriced++;
  }
 }
 return {linked:attached+unpriced,unpriced,blocked,missing,stale:!sameRoster};
}
function compare(a,b){const x=a.r?.v3_operational,y=b.r?.v3_operational;
 return (y?.score??-1)-(x?.score??-1)||(y?.available??0)-(x?.available??0)||String(a.r?.code).localeCompare(String(b.r?.code));}
function composite(r){const v=r?.v3_operational;
 return {earned:v?.base??null,available:v?.available??0,pillars:Object.entries(BUDGET).map(([key,max])=>({key,label:LABELS[key],max,
  candidate:v?.areas?.[key]?.available?v.areas[key].points:null,available:v?.areas?.[key]?.available??0}))};}
function missingComponents(v){
 if(!v||!v.parts)return [];
 const gaps=[];
 for(const [area,components] of Object.entries(MAXES)){
  for(const [key,maximum] of Object.entries(components)){
   const receipt=v.parts[area]?.[key];
   const observed=Array.isArray(receipt)&&finite(receipt[1])?receipt[1]:0;
   const missing=maximum-observed;
   if(missing>0.00002)gaps.push({area,key,missing,partiallyObserved:observed>0});
  }
 }
 return gaps;
}
function missingMarkup(v){
 const gaps=missingComponents(v);
 if(!gaps.length)return '<p class="u-muted">평가항목 100점 배점의 근거가 모두 관측되었습니다. 원천 독립검증·실제 성과 인증은 별도입니다.</p>';
 return '<details class="u-operational-missing"><summary>미관측 항목 '+gaps.length+'개 · 부족한 근거 '+(100-v.available).toFixed(1)+' / 100점</summary>'+
  '<p class="u-muted">미관측은 0점이 아닙니다. 아래 숫자는 점수 손실이 아닌 아직 확보되지 않은 배점입니다. 구체적인 원천 결손 사유는 검증 중입니다.</p><ul>'+
  gaps.map(g=>'<li>'+esc(LABELS[g.area])+' · '+esc(COMPONENTS[g.key])+' · '+g.missing.toFixed(2)+'점 '+(g.partiallyObserved?'추가 근거 필요':'근거 미관측')+'</li>').join('')+'</ul></details>';
}
// The five areas are GROSS earned points. The stored R1d score is NET of
// capital, structural-stop and financial-risk adjustments; the same source
// reconciliation is rendered in both the stock list and the detail panel.
function scoreReconciliation(v){
 if(!v||![v.base,v.score,v.capital_deduction,v.stop_adjustment,v.risk_deduction].every(finite))return null;
 return {gross:v.base,capital:v.capital_deduction,stop:v.stop_adjustment,
  risk:v.risk_deduction,netDeduction:v.capital_deduction-v.stop_adjustment+v.risk_deduction,
  final:v.score};
}
function reconciliationMarkup(v,compact=false){
 const s=scoreReconciliation(v);if(!s)return '';
 const n=value=>value.toFixed(1);
 const signed=value=>(value<0?'−':value>0?'+':'')+n(Math.abs(value));
 const netLabel=s.netDeduction>0?'순감점 −'+n(s.netDeduction):
  s.netDeduction<0?'순가점 +'+n(-s.netDeduction):'가감점 0.0';
 return '<div class="u-score-reconcile'+(compact?' u-score-reconcile-compact':'')+'" aria-label="운영점수 산출 및 가감점">'+
  '<span>영역 합계 <b>'+n(s.gross)+'</b></span>'+
  '<span class="'+(s.capital>0?'u-score-deduction':'')+'">시총 '+(s.capital>0?'−':'')+n(s.capital)+'</span>'+
  '<span class="'+(s.stop<0?'u-score-deduction':s.stop>0?'u-score-bonus':'')+'">손절거리 '+signed(s.stop)+'</span>'+
  '<span class="'+(s.risk>0?'u-score-deduction':'')+'">재무위험 '+(s.risk>0?'−':'')+n(s.risk)+'</span>'+
  '<span class="u-score-adjustment">'+netLabel+'</span>'+
  '<strong>최종 '+n(s.final)+'점</strong></div>';
}
function markup(r){const v=r?.v3_operational;if(!v)return r?.v3_operational_identity_blocked?
 '<p class="u-muted">종목명 또는 시장구분 불일치 · 이 종목의 신규 운영점수 연결만 보류합니다. 기존 모델은 비교용으로 유지합니다.</p>':
 r?.v3_operational_evidence_missing?
 '<p class="u-muted">이 종목의 신규 운영점수 근거가 현재 저장본에 없습니다. 다른 종목의 과거 점수를 대입하지 않습니다.</p>':
 '<p class="u-muted">신규 운영점수 연결 대기 · 기존 모델은 아래 비교용으로 유지합니다.</p>';
 return '<section class="u-operational-detail"><h3>잠정 운영점수 · '+(r.v3_operational_version===VERSION?'R1d':r.v3_operational_version==='v3-stable-r1c-operational-research'?'R1c':r.v3_operational_version==='v3-stable-r1b-operational-research'?'R1b':'R1a')+'</h3><p><strong>'+(v.score===null?'미관측':v.score.toFixed(1)+' / 100')+
  '</strong> · 확보배점 '+v.available.toFixed(1)+' / 100 · 기준일 '+esc(v.asof)+' · 최초 관측 '+esc(v.oldest_asof)+
  '</p>'+reconciliationMarkup(v)+
  '<p class="u-muted">재무 자료일 '+esc(v.risk_asof||'미확인')+' (확인된 위험 하한 · '+(v.risk_status==='partial'?'일부 확인':'위험 미확인')+')</p>'+
  (v.risk_warnings.length?'<p class="u-muted">'+v.risk_warnings.map(esc).join(' · ')+'</p>':'')+
  '<p>기존 모델 '+(finite(v.legacy_score)?v.legacy_score.toFixed(1)+'점':'미관측')+' · 신규 '+(v.rank??'—')+'위 / 기존 '+(v.legacy_rank??'—')+'위</p>'+
  (finite(v.prior_operational_score)?'<p class="u-muted">직전 운영모델 '+v.prior_operational_score.toFixed(1)+'점 · '+(v.prior_operational_rank??'—')+'위 (점수 계산식 변경 전 비교)</p>':'')+
  '<table><thead><tr><th>영역</th><th>점수</th><th>확보/전체</th></tr></thead><tbody>'+Object.entries(BUDGET).map(([a,max])=>
   '<tr><td>'+LABELS[a]+'</td><td>'+(v.areas[a].available?v.areas[a].points.toFixed(2):'미관측')+'</td><td>'+v.areas[a].available.toFixed(1)+' / '+max+'</td></tr>').join('')+
  (scoreReconciliation(v)?'<tr class="u-score-table-gross"><th scope="row">영역 합계 (감점 전)</th><td>'+v.base.toFixed(2)+'</td><td>'+v.available.toFixed(1)+' / 100</td></tr>'+
   '<tr class="u-score-table-deduction"><th scope="row">시총·손절거리·재무위험 순가감점</th><td>'+
    (scoreReconciliation(v).netDeduction>0?'−':scoreReconciliation(v).netDeduction<0?'+':'')+
    Math.abs(scoreReconciliation(v).netDeduction).toFixed(2)+'</td><td>점수 조정 (확보배점 아님)</td></tr>'+
   '<tr class="u-score-table-final"><th scope="row">최종 운영점수</th><td>'+v.score.toFixed(1)+'</td><td>/ 100</td></tr>':'')+
  '</tbody></table><p class="u-muted">영역별 표시 점수는 소수 둘째 자리로 반올림되어 합계와 약간 다를 수 있습니다. 최종점수는 반올림 전 원본으로 계산합니다.</p>'+
  missingMarkup(v)+
  '<details><summary>신규 점수 세부 근거</summary>'+Object.entries(v.parts).map(([a,ps])=>'<h4>'+LABELS[a]+'</h4><ul>'+Object.entries(ps).map(([k,p])=>
   '<li>'+esc(COMPONENTS[k])+': '+p[0].toFixed(2)+' / '+p[1].toFixed(2)+' · '+esc(p[2])+'</li>').join('')+'</ul>').join('')+
  '<p>MA 기울기 전환은 종목별 일·주봉 가격 이력이 준비된 경우에만 관측합니다. 거래 구조 12점은 완성된 장기 거래대금 자료가 있을 때만 분해하여 반영하고, 독립 돌파 확인 1.2점은 60거래일 고점 돌파 뒤 5일 이상 가격 유지가 관측된 경우에만 반영합니다. 부채 상환·반복 현금흐름 위험은 검증 중입니다. 결측을 100점으로 환산하지 않습니다.</p></details>'+
  '<p class="u-muted">단계적 전환 모델 · 과거 성과·손절확률 미검증 · 기존 점수 이력 별도 유지</p></section>';
}
const api={VERSION,BUDGET,attach,attachAllListed,validRow,compare,composite,missingComponents,scoreReconciliation,reconciliationMarkup,markup};
root.V3OperationalScore=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
