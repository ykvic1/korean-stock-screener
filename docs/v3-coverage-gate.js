(function(){'use strict';
const root=document.getElementById('coverageGateRows');
const status=document.getElementById('coverageGateStatus');
if(!root||!status)return;
const areas=[['technical','기술'],['volume','거래량·OBV'],['momentum','모멘텀'],['financial','재무'],['valuation','가치평가'],['industry','산업']];
const safeCount=(v,total)=>Number.isInteger(v)&&v>=0&&v<=total?v:null;
const load=path=>fetch(path,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json()});
Promise.all([load('v3-current-observed-all-listed-summary.json'),load('v3-real-source-six-area-coverage.json'),load('v3-krx-derived-backfill-progress.json'),load('v3-industry-saved-profit-gap-summary.json'),load('v3-industry-observed-relative-strength.json')])
 .then(([d,e,h,g,i])=>{
  const total=d.listed_total,counts=d.observed_issuer_counts;
  if(!Number.isInteger(total)||total<=0||!counts||!areas.every(([k])=>safeCount(counts[k],total)!==null))throw Error('invalid coverage payload');
  status.textContent='관측 기준 '+String(d.asof||'미확인')+' · 대상 '+total.toLocaleString('ko-KR')+'종목 · 확정 100점 '+String(d.certified_100point_count??'미확인')+'종목';
  if(e.listed_total!==total||e.market_asof!==d.asof||g.asof!==d.asof||i.asof!==d.asof||i.listed_total!==total)throw Error('source dates or universe differ');
  root.replaceChildren();
  for(const [key,label] of areas){
   const n=counts[key],el=document.createElement('div');
   const pct=(100*n/total).toFixed(1);
   const a=document.createElement('span');a.textContent=label;
   const b=document.createElement('strong');b.textContent=n.toLocaleString('ko-KR')+' / '+total.toLocaleString('ko-KR');
   const c=document.createElement('small');c.textContent=pct+'% 잠정 산출';
   el.append(a,b,c);root.append(el);
  }
  const add=(label,value,note)=>{const el=document.createElement('div');const a=document.createElement('span');a.textContent=label;const b=document.createElement('strong');b.textContent=value;const c=document.createElement('small');c.textContent=note;el.append(a,b,c);root.append(el);};
  const num=v=>Number.isInteger(v)&&v>=0?v.toLocaleString('ko-KR'):'미확인';
  add('DART 4개년 실적 관측',num(e.verified_four_year_income_reference_issuers)+' / '+num(total),'공시 실적 참고자료 · PIT 확정 아님');
  add('공식 기관·외국인 수급',num(e.official_investor_IAS_source_issuer_count)+' / '+num(total),'거래량·OBV와 별개 · IAS 20점 검증');
  add('공식 PBR 검증',num(e.official_pbr_verified_count)+' / '+num(total),'잠정 PBR 산출과 별개');
  add('산업 실적 비교 확보',num(g.actual_saved_annual_profit_pairs)+' / '+num(total),'2024~2025 공시 영업이익 비교 · 공식 산업점수 아님');
  add('산업 상대강도 관측',num(i.paired_20_60_observations)+' / '+num(total),'20·60일 근사 상대강도 · 업종구성 PIT 미검증');
  add('KRX 과거 수집 구간',num(h.five_session_cohorts_completed)+' / '+num(h.five_session_cohorts_target),'수집 구간 수 · 종목별 검증 완료율 아님');
 }).catch(e=>{status.textContent='확보율 자료 확인 실패 · 점수 검증 상태 미확인 ('+String(e.message)+')';});
})();