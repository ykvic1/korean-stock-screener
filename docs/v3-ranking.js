/* V3 score-first user research: published v2.2 points + matched quant. No fabricated V3 points. */
(function(root){
'use strict';
const PERIODS=['초단기 1주','단기 2주','스윙 1개월','스윙 2개월','중기 3개월','중기 6개월','장기 1년'];
const esc=x=>String(x==null?'':x).replace(/[&<>"']/g,t=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[t]));
const finite=x=>typeof x==='number'&&Number.isFinite(x);
const money=x=>finite(x)?Math.round(x).toLocaleString('ko-KR')+'원':'미확인';
const pct=x=>finite(x)?(x>=0?'+':'')+x.toFixed(1)+'%':'미산출';
const eok=x=>finite(x)?(x/100000000).toLocaleString('ko-KR',{maximumFractionDigits:1})+'억원':'미확인';
function observedAnnuals(official,asof){
 const byCode=new Map();
 if(!official||official.asof!==asof||!['observed','completed'].includes(official.dart?.status)
    ||!Array.isArray(official.dart.stocks))return byCode;
 for(const stock of official.dart.stocks){
  if(!stock||!/^[0-9]{6}$/.test(String(stock.code))||
    stock.status!=='observed'||!Array.isArray(stock.reports))continue;
  const annual=stock.reports.find(x=>x&&Number.isInteger(x.year)&&
    x.year<=Number(asof.slice(0,4))&&x.year>=2010&&
    (x.fs_div==='CFS'||x.fs_div==='OFS')&&x.currency==='KRW'&&
    typeof x.values==='object'&&x.values!==null);
  if(annual)byCode.set(stock.code,annual);
 }
 return byCode;
}
function stopDistance(r){
  if(!finite(r?.price)||!finite(r?.stop_line)||r.price<=0||r.stop_line<=0||r.stop_line>=r.price)return null;
  const p=100*(r.price-r.stop_line)/r.price;
  return finite(r.stop_pct)&&Math.abs(r.stop_pct+p)>2?null:+p.toFixed(2);
}
function highRisk(r){
 return (Array.isArray(r.risk_flags)?r.risk_flags:[]).some(s=>
   ['손절폭 과대','단기 급등','추격 주의','장기 추세 취약','거래대금 부족','급격한 거래량 집중'].some(x=>String(s).includes(x)))||
   (finite(r.price60)&&r.price60>=40);
}
function observedScore(r,weights){
 if(!finite(r.score)||!finite(r.coverage_pct)||r.score<0||r.score>100||r.coverage_pct<0||r.coverage_pct>100||
    !weights||typeof weights!=='object'||!r.components)return false;
 const entries=Object.entries(weights);if(entries.length!==8)return false;
 let obs=0,max=0;
 for(const [k,w] of entries){
   if(!finite(w)||w<=0||!Object.prototype.hasOwnProperty.call(r.components,k))return false;
   const v=r.components[k];
   if(v!==null&&(!finite(v)||v<0||v>w))return false;
   if(v!==null){obs+=v;max+=w;}
 }
 return max>0&&Math.abs(r.score-obs/max*100)<=1.1&&
   Math.abs(r.coverage_pct-max)<=1.1;
}
function period(q,name){
 const p=q?.periods?.[name];
 return q?.status==='exploratory'&&p?.status==='exploratory'&&
 finite(p.sample_n)&&p.sample_n>=40&&finite(p.symbol_n)&&p.symbol_n>=10&&
 finite(p.quarter_n)&&p.quarter_n>=12&&finite(p.mean_net_return_pct)&&
 finite(p.stop_first_pct)&&finite(p.max_adverse_p75_pct)?p:null;
}
function decision(row,q){
 const risk=stopDistance(row);
 if(highRisk(row))return {label:'매수 보류',reason:'고점 추격·과열 또는 하락 위험 신호',level:'wait'};
 if(risk===null||risk<3||risk>15)return {label:'매수 보류',reason:'구조 참고 손절선과 현재가의 거리가 부적합',level:'wait'};
 if(row.coverage_pct<55)return {label:'추가 검증',reason:'기존 점수의 실측 가중치 55% 미만',level:'wait'};
 if(!q||q.status!=='exploratory')return {label:'퀀트 대기',reason:'해당 종목의 현재 기준일 퀀트 연구 없음',level:'review'};
 const p=['스윙 1개월','스윙 2개월','중기 3개월','중기 6개월'].map(k=>period(q,k)).filter(Boolean);
 if(!p.length)return {label:'퀀트 대기',reason:'유효한 과거 유사조건 표본 부족',level:'review'};
 // A small positive average does not offset high stop-first frequency.
 // These are exploratory historical thresholds, not calibrated forecasts.
 if(p.every(z=>z.stop_first_pct>=40||z.mean_net_return_pct<=0))
   return {label:'매수 보류',reason:'과거 유사조건의 수익성 부진·손절 선도달 위험 우세',level:'wait'};
 if(p.some(z=>z.mean_net_return_pct>0&&z.stop_first_pct<=35&&z.max_adverse_p75_pct<=15))
   return {label:'조건부 모의 검토',reason:'과거 연구 일부 우호적; 차트·수급·실적 추가 확인',level:'review'};
 return {label:'관망·추가 확인',reason:'과거 유사조건의 위험과 수익이 혼재',level:'review'};
}
function prepare(data,quant,recs,official,priorityQuant){
 if(!data||data.version!=='2.0'||data.status!=='success'||!/^\d{4}-\d{2}-\d{2}$/.test(data.date)||
    !Array.isArray(data.stocks)||data.stocks.length<1||data.stocks.length>6000)
    throw Error('invalid_daily_observations');
 const matched=!!quant&&quant.status==='exploratory'&&quant.asof===data.date&&Array.isArray(quant.stocks);
 const qmap=new Map((matched?quant.stocks:[]).filter(q=>q&&q.status==='exploratory'&&
    q.reference_price_date===data.date).map(q=>[q.code,q]));
 // The shortlist comes from TODAY's price/score snapshot; its attached
 // historical quant may still be yesterday's. Show current candidates while
 // keeping study and DART data strictly gated to current source date.
 const matchedRec=!!recs&&recs.asof===data.date&&
   Array.isArray(recs.dossiers);
 const rmap=new Map((matchedRec?recs.dossiers:[]).map(r=>[r.code,r]));
 const priorityMatched=!!priorityQuant&&
   priorityQuant.schema==='v3-targeted-live-shortlist-quant-v1'&&
   priorityQuant.asof===data.date&&priorityQuant.status==='exploratory'&&
   priorityQuant.raw_ohlcv_copied===false&&
   priorityQuant.verified_predictive_accuracy===false&&
   finite(priorityQuant.peer_cohort_valid)&&priorityQuant.peer_cohort_valid>=20&&
   Array.isArray(priorityQuant.stocks)&&priorityQuant.stocks.length<=20;
 const pmap=new Map((priorityMatched?priorityQuant.stocks:[]).filter(x=>
    x&&x.reference_price_date===data.date&&
    (x.status==='exploratory'||x.status==='insufficient')&&
    /^[0-9]{6}$/.test(String(x.code))&&rmap.has(x.code)&&
    finite(x.peer_cohort_requested)&&x.peer_cohort_requested>=20).map(x=>[x.code,x]));
 const financials=observedAnnuals(official,data.date);
 const rows=[],seen=new Set();
 for(const r of data.stocks){
   if(!r||!/^\d{6}$/.test(String(r.code))||seen.has(r.code)||!finite(r.price)||r.price<=0||
      !['KOSPI','KOSDAQ'].includes(r.market)||!observedScore(r,data.weights))continue;
   seen.add(r.code);
   const rec=rmap.get(r.code)||null;
   const standard=qmap.get(r.code)||null,priority=pmap.get(r.code)||null;
   const standardUsable=standard&&['스윙 1개월','스윙 2개월','중기 3개월','중기 6개월']
     .some(k=>period(standard,k));
   const q=standardUsable?standard:(priority||standard);
   const quantSource=priority&&q===priority?
     '추천 우선 연구 '+priority.peer_cohort_requested+'종목 요청 / '+priority.peer_cohort_valid+'종목 실제 이력 확보':
     standard&&q===standard?'일반 연구 최대 120종목 표본':'연구 자료 없음';
   const qualified=!!rec&&rec.qualification==='needs_deeper_evidence'&&!highRisk(r);
   const caution=!!rec&&!qualified;
   const observedDecision=decision(r,q);
   const finalDecision=caution&&observedDecision.label!=='매수 보류'?
     {label:'매수 보류',reason:'위험 경고·연구 전용 후보 — '+observedDecision.reason,level:'wait'}:
     observedDecision;
   rows.push({r,q,rec,quantSource,financialRef:financials.get(r.code)||null,quant:!!q,
     researchCandidate:!!rec,qualified,caution,
     risk:stopDistance(r),highRisk:highRisk(r),decision:finalDecision});
 }
 // Only source-dated, published dossiers are considered shortlisted.
 // Cautionary names are NOT promoted to buy status; show them separately
 // after standard research candidates rather than quietly hiding them.
 rows.sort((a,b)=>Number(b.researchCandidate)-Number(a.researchCandidate)||
  Number(b.qualified)-Number(a.qualified)||
  Number(a.highRisk)-Number(b.highRisk)||b.r.score-a.r.score||
  b.r.coverage_pct-a.r.coverage_pct||a.r.code.localeCompare(b.r.code));
 const selected=rows.filter(x=>x.researchCandidate);
 const researchReady=x=>x.quant&&
   ['스윙 1개월','스윙 2개월','중기 3개월','중기 6개월'].some(k=>period(x.q,k));
 const withStudy=selected.filter(x=>x.quant).length;
 const withUsable=selected.filter(researchReady).length;
 return {date:data.date,rows,weights:data.weights,marketEligible:data.eligible_count,
  quantCurrent:matched,quantAsOf:quant?.asof||null,
  priorityQuantCurrent:priorityMatched,priorityQuantAsOf:priorityQuant?.asof||null,
  priorityQuantRequested:priorityMatched?priorityQuant.peer_cohort_requested:null,
  priorityQuantValid:priorityMatched?priorityQuant.peer_cohort_valid:null,
  quantCount:rows.filter(x=>x.quant).length,recommendationCount:selected.length,
  recommendationQuantCount:withStudy,recommendationUsableQuantCount:withUsable,
  recommendationInsufficientCount:selected.filter(x=>x.quant&&!researchReady(x)).length,
  recommendationCautionCount:selected.filter(x=>x.caution).length,
  recommendationAwaitingCount:selected.length-withStudy,
  financialReferenceCount:rows.filter(x=>x.financialRef!==null).length,
  recommendationFinancialCount:selected.filter(x=>x.financialRef!==null).length};
}
function select(data,mode,market='all',search=''){
 const query=String(search).toLocaleLowerCase('ko-KR').trim();
 return data.rows.filter(x=>(market==='all'||market===x.r.market)&&
  (!query||(x.r.name+' '+x.r.code+' '+(x.r.sector||'')).toLocaleLowerCase('ko-KR').includes(query))&&
  (mode==='recommended'?x.researchCandidate:mode==='quant'?x.quant:
   mode==='early'?(!x.highRisk&&x.risk!==null&&x.risk>=3&&x.risk<=15&&/초기|전환|수렴|바닥/.test(String(x.r.phase))&&x.r.coverage_pct>=55):true));
}
function quantGlance(q){
 if(!q)return '당일 퀀트 미확보';
 const items=[['스윙 1개월','1개월'],['중기 3개월','3개월'],['중기 6개월','6개월']]
  .map(([key,label])=>{const p=period(q,key);return p?
   label+' 평균 '+pct(p.mean_net_return_pct)+' / 손절 먼저 '+pct(p.stop_first_pct)+' (표본 '+p.sample_n+'건)':null;
  }).filter(Boolean);
 return items.length?items.join(' · '):'유효 표본 미충족 — 매수 근거 불충분';
}
const core={PERIODS,stopDistance,highRisk,observedScore,period,decision,prepare,select,observedAnnuals,quantGlance,esc,money,pct,eok};
if(typeof module!=='undefined'&&module.exports)module.exports=core;
root.V3RankedCore=core;
if(!root.document)return;
function init(){
 const D=root.document,$=id=>D.getElementById(id);
 let model=null,mode='recommended',limit=24;
 const status=$('rankStatus'),cards=$('rankCards'),details=$('rankDetails');
 function render(){
  if(!model)return;
  const rows=select(model,mode,$('marketFilter').value,$('rankSearch').value);
  $('rankCount').textContent='해당 조건 '+rows.length+'종목 · 점수 산출 '+model.rows.length+
    '종목 · 최신 퀀트 연결 '+model.quantCount+'종목';
  cards.innerHTML=rows.length?rows.slice(0,limit).map((x,i)=>{
   const r=x.r;
   return '<article class="rank-card"><div class="top"><div>'+
     '<span class="rank-number">'+(i+1)+'</span> <strong>'+esc(r.name)+'</strong>'+
     '<p class="sub">'+esc(r.code)+' · '+esc(r.market)+' · '+esc(r.phase||'위치 미확인')+'</p></div>'+
     '<div class="rank-score">'+r.score.toFixed(1)+'점<small>v2.2 관측점수<br>확보 '+r.coverage_pct.toFixed(0)+'%</small></div></div>'+
     (x.caution?'<p class="rank-caution"><b>위험 경고·연구 전용:</b> 매수 후보로 승격되지 않았습니다.</p>':'')+
     '<div class="rank-metrics"><div><small>종가</small><b>'+money(r.price)+'</b></div>'+
     '<div><small>손절 참고선</small><b>'+money(r.stop_line)+'</b></div>'+
     '<div><small>현재 대응</small><b>'+esc(x.decision.label)+'</b></div>'+
     '<div><small>퀀트 연구</small><b>'+(x.quant?
       (['스윙 1개월','스윙 2개월','중기 3개월','중기 6개월'].some(k=>period(x.q,k))?
        '유사통계 있음':'표본 미충족'):'최신 연구 대기')+'</b></div></div>'+
     '<p class="rank-quant-line">'+esc(quantGlance(x.q))+'</p>'+
     '<div class="rank-foot"><span>'+esc(x.decision.reason)+'</span>'+
     '<button type="button" data-show="'+esc(r.code)+'">점수·퀀트 검증 보기</button></div></article>';
  }).join(''):'<p class="empty">조건에 해당하는 연구 후보가 없습니다. 추천 수를 억지로 채우지 않았습니다.</p>';
  $('rankMore').hidden=rows.length<=limit;
  D.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));
 }
 function show(code){
  const x=model?.rows.find(t=>t.r.code===code);if(!x)return;
  const r=x.r,risk=x.risk,flags=Array.isArray(r.risk_flags)?r.risk_flags:[];
  const parts=Object.entries(model.weights).map(([name,max])=>{
   const v=r.components[name];
   return '<div class="factor"><span>'+esc(name)+'</span><b>'+
       (finite(v)?v.toFixed(1)+' / '+max+'점':'미확인 / '+max+'점')+'</b></div>';
  }).join('');
  let quant='<p class="note">해당 종목의 퀀트 연구가 없거나 기준일이 다릅니다. 과거 통계를 현재 매수 조건에 끼워넣지 않습니다.</p>';
  if(x.quant){
   quant='<div class="quant-scroller"><table><thead><tr><th>기간</th><th>표본</th>'+
    '<th>상승 빈도</th><th>평균 순수익</th><th>목표 선도달</th>'+
    '<th>손절 선도달</th><th>역행폭 P75</th><th>표본외 결과</th></tr></thead><tbody>'+
    PERIODS.map(h=>{
      const v=period(x.q,h);
      if(!v){
        const p=x.q?.periods?.[h];
        const diagnostic=p&&finite(p.sample_n)?
          '분석 보류 — 사례 '+p.sample_n+'건 / 독립 종목 '+
          (finite(p.symbol_n)?p.symbol_n:'미확인')+'개 / 기간 분산 '+
          (finite(p.quarter_n)?p.quarter_n:'미확인')+'분기':
          '분석 표본 부족 / 미산출';
        return '<tr><th>'+esc(h)+'</th><td colspan="7">'+esc(diagnostic)+'</td></tr>';
      }
      return '<tr><th>'+esc(h)+'</th><td>'+v.sample_n+'건</td><td>'+pct(v.up_pct)+
       '</td><td>'+pct(v.mean_net_return_pct)+'</td><td>'+pct(v.target_first_pct)+
       '</td><td>'+pct(v.stop_first_pct)+'</td><td>'+pct(v.max_adverse_p75_pct)+
       '</td><td>'+(finite(v.chronological_holdout?.test_mean_net_return_pct)?
        pct(v.chronological_holdout.test_mean_net_return_pct):'미검증')+'</td></tr>';
    }).join('')+'</tbody></table></div>';
  }
  details.innerHTML='<div class="rank-detail-head"><div><h2>'+esc(r.name)+' · '+esc(r.code)+'</h2>'+
   '<p class="sub">'+esc(r.market)+' · 종가 기준 '+esc(model.date)+'</p></div>'+
   '<button type="button" id="rankClose">닫기</button></div>'+
   '<div class="rank-verdict"><b>현재 판단: '+esc(x.decision.label)+'</b><p>'+
   esc(x.decision.reason)+'</p></div>'+
   '<h3>① 기존 가중치 점수 분해</h3>'+
   '<p>기존 v2.2 점수 '+r.score.toFixed(1)+' / 100 · 관측된 가중치 '+
   r.coverage_pct.toFixed(0)+'%. <b>v3의 IAS25·재무20·가치15·기술15·업종15·모멘텀10 점수와 다릅니다.</b></p>'+
   '<div class="factor-grid">'+parts+'</div>'+
   '<h3>② 매수 무효화 기준과 하락 위험</h3><div class="rank-metrics">'+
   '<div><small>종가</small><b>'+money(r.price)+'</b></div>'+
   '<div><small>구조 손절 참고선</small><b>'+money(r.stop_line)+'</b></div>'+
   '<div><small>참고 손절거리</small><b>'+(risk===null?'미확인':pct(-risk))+'</b></div>'+
   '<div><small>PBR 참고값</small><b>'+(finite(r.pbr)?r.pbr.toFixed(2)+'배':'미확인')+'</b></div></div>'+
   '<p class="note">위험 경고: '+(flags.length?esc(flags.join(' · ')):'추가 위험 경고 등록 없음')+
   '. 실제 손절 체결 가격·손실은 이론값보다 나쁠 수 있습니다.</p>'+
   '<h3>③ OpenDART 연간 실적 참고자료</h3>'+
   (x.financialRef?
     '<p class="note">공시 '+x.financialRef.year+'년 '+esc(x.financialRef.fs_div)+
       ' · 접수번호 '+esc(x.financialRef.receipt_id||'미확인')+
       ' · 원화 연간 기준, 정정공시·과거 시점 정보 검증 전</p>'+
     '<div class="rank-metrics">'+
      '<div><small>매출액</small><b>'+eok(x.financialRef.values.revenue)+'</b></div>'+
      '<div><small>영업이익</small><b>'+eok(x.financialRef.values.operating_income)+'</b></div>'+
      '<div><small>영업현금흐름</small><b>'+eok(x.financialRef.values.operating_cashflow)+'</b></div>'+
      '<div><small>부채비율</small><b>'+
       (finite(x.financialRef.values.debt_to_equity_pct)?
         x.financialRef.values.debt_to_equity_pct.toFixed(1)+'%':'미확인')+
      '</b></div></div>':
     '<p class="note">해당 종목의 접수일 확인 가능한 연간 공시 재무 참고자료가 아직 연결되지 않았습니다. 미확인 값을 임의 점수로 채우지 않습니다.</p>')+
   '<h3>④ 과거 유사조건 퀀트 연구</h3>'+
   '<p class="note">통계 모집단: '+esc(x.quantSource)+
   '. 서로 다른 연구 모집단의 수익률을 직접 동일 조건으로 비교할 수 없습니다.</p>'+quant+
   '<p class="note">연구 통계는 유사한 과거 사례의 기술적 분포일 뿐 미래 상승 확률이 아닙니다. 수익률은 왕복비용 0.4%p 가정, 과거 상장폐지 제외·미수정 가격/시장 동조성 등 한계가 있습니다. 표본외 수익률이 미검증이면 절대로 검증됐다고 표시하지 않습니다.</p>'+
   '<h3>⑤ 매수 판단 가설</h3><p>매수 검토: 패턴 돌파·거래량 지속·주봉 안착, 재무와 수급 개선 확인.<br>'+
   '관망: 과거 퀀트의 손절빈도 상승 또는 재무·수급 부재.<br>'+
   '무효화: 구조 참고선 이탈 시 차트/업황 가설을 다시 검토.</p>'+
   '<p class="note">확정 매수 추천이 아니며, v3 공식 6영역 총점과 1·2·3·6·12개월 기대수익률은 실측 데이터 검증 전 미산출입니다.</p>';
  details.hidden=false;
  $('rankClose').addEventListener('click',()=>{details.hidden=true;});
  details.scrollIntoView({block:'start',behavior:'smooth'});
 }
 async function load(){
  status.textContent='전 시장 점수와 퀀트 자료 조회 중…';
  try{
   const urls=['data.json','quant.json','recommendations.json','official.json','v3-priority-quant.json'];
   const src=await Promise.all(urls.map(async name=>{
    try{const r=await root.fetch(name+'?rank='+Date.now(),{cache:'no-store'});return r.ok?await r.json():null}
    catch(_){return null;}
   }));
   model=prepare(...src);
   status.textContent='기준일 '+model.date+' · 점수 산출 '+model.rows.length+
     '종목 / 전체 분석 대상 '+(model.marketEligible||'미확인')+
     '종목 · 심층 선별 '+model.recommendationCount+
     '종목 · 동일 기준일 퀀트 '+model.quantCount+'종목';
   status.className='status';
   $('rankQuantStatus').textContent=model.priorityQuantCurrent?
     '추천 후보 우선 실데이터 연구 '+model.priorityQuantAsOf+
       ' · '+model.priorityQuantRequested+'종목 수집 시도 / '+model.priorityQuantValid+
       '종목 유효 가격 이력 (미래 수익률 예측 미검증)':
     model.quantCurrent?
     '일반 퀀트 기준일 '+model.quantAsOf+
       ' · 역사적 유사조건 참고 통계, 미래 수익률 예측 검증 전':
     '퀀트 기준일 불일치 또는 미확보: 최신 추천의 퀀트 검증은 대기';
   $('rankCandidateQuant').textContent='연간 공시 참고자료 '+model.recommendationFinancialCount+
     '/'+model.recommendationCount+'개 · 심층 선별 후보 '+model.recommendationCount+
     '종목 (위험 경고·연구 전용 '+model.recommendationCautionCount+'종목 포함)'+
     ' · 당일 퀀트 연결 '+model.recommendationQuantCount+
     '종목 · 기간별 유효 통계 '+model.recommendationUsableQuantCount+
     '종목 · 표본 미충족 '+model.recommendationInsufficientCount+
     '종목 · 자료 갱신 대기 '+model.recommendationAwaitingCount+
     '종목. 유사통계가 있어도 검증된 미래 수익률은 아닙니다.';
   render();
  }catch(_){
   model=null;status.className='status error';
   status.textContent='정상 전 종목 점수 자료 미확보. 허위 추천을 표시하지 않습니다.';
   $('rankCandidateQuant').textContent='추천 후보 및 퀀트 연계 검증 불가';
   cards.innerHTML='<p class="empty">유효한 전시장 데이터가 필요합니다.</p>';
  }
 }
 $('rankModes').addEventListener('click',e=>{const b=e.target.closest('[data-mode]');
  if(b){mode=b.dataset.mode;limit=24;render();}});
 cards.addEventListener('click',e=>{const b=e.target.closest('[data-show]');if(b)show(b.dataset.show);});
 $('rankMore').addEventListener('click',()=>{limit+=24;render();});
 $('marketFilter').addEventListener('change',()=>{limit=24;render();});
 $('rankSearch').addEventListener('input',()=>{limit=24;render();});
 $('rankReload').addEventListener('click',load);
 load();
}
root.document.addEventListener('DOMContentLoaded',init);
})(typeof window!=='undefined'?window:globalThis);