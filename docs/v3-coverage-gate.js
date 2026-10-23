(function(){'use strict';
const root=document.getElementById('coverageGateRows');
const status=document.getElementById('coverageGateStatus');
if(!root||!status)return;
const areas=[['technical','기술'],['volume','거래량·OBV'],['momentum','모멘텀'],['financial','재무'],['valuation','가치평가'],['industry','산업']];
const safeCount=(v,total)=>Number.isInteger(v)&&v>=0&&v<=total?v:null;
fetch('v3-current-observed-all-listed-summary.json',{cache:'no-store'})
 .then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json()})
 .then(d=>{
  const total=d.listed_total,counts=d.observed_issuer_counts;
  if(!Number.isInteger(total)||total<=0||!counts||!areas.every(([k])=>safeCount(counts[k],total)!==null))throw Error('invalid coverage payload');
  status.textContent='관측 기준 '+String(d.asof||'미확인')+' · 대상 '+total.toLocaleString('ko-KR')+'종목 · 확정 100점 '+String(d.certified_100point_count??'미확인')+'종목';
  root.replaceChildren();
  for(const [key,label] of areas){
   const n=counts[key],el=document.createElement('div');
   const pct=(100*n/total).toFixed(1);
   const a=document.createElement('span');a.textContent=label;
   const b=document.createElement('strong');b.textContent=n.toLocaleString('ko-KR')+' / '+total.toLocaleString('ko-KR');
   const c=document.createElement('small');c.textContent=pct+'% 잠정 산출';
   el.append(a,b,c);root.append(el);
  }
 }).catch(e=>{status.textContent='확보율 자료 확인 실패 · 점수 검증 상태 미확인 ('+String(e.message)+')';});
})();