(function(root){
'use strict';
const core=typeof module!=='undefined'&&module.exports?require('./workspace.js'):root.V3UnifiedCore;
const day=x=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&Number.isFinite(Date.parse(x+'T00:00:00Z'))&&new Date(x+'T00:00:00Z').toISOString().slice(0,10)===x;
const states={insufficient_sample:'표본 부족',early_relative_recovery_watch:'회복 관찰',relative20_extension_caution:'20일 과열 주의',no_early_recovery_pattern_observed:'회복 조건 미충족'};
const num=x=>typeof x==='number'&&Number.isFinite(x);
const dualLabels={dual_evidence_watch:'가격·실적 동시 회복',price_only_watch:'가격 회복 관찰',earnings_only_watch:'실적 확산 관찰',not_dual_evidence_watch:'조건 미충족'};
function profitEvidence(raw,dual,rows){
 if(!dual||dual.schema!=='v3-current-company-industry-dual-evidence-watch-v1'||dual.asof!==raw.asof||
 dual.listed_total!==raw.listed_total||dual.current_company_code_market_groups!==rows.length||
 dual.price_relative20_60_verified_samples!==raw.paired_20_60_observations||
 dual.price_recovery_watch_groups!==raw.groups_early_recovery_watch||
 dual.source_sha256?.catalog!==raw.source_sha256.catalog||
 dual.source_sha256?.current_dart_company_industry!==raw.source_sha256.current_dart_company_industry_codes||
 dual.historical_krx_sector_pit_verified!==false||dual.official_industry15_points_authorized!==false||
 dual.investable_stock_screener_score_authorized!==false||dual.external_provider_calls!==0||
 dual.minimum_group_issuer_count!==10||dual.minimum_each_source_coverage!==.75||
 dual.minimum_profit_breadth_lower_bound!==.5||!Array.isArray(dual.groups)||dual.groups.length!==rows.length)return null;
 const byKey=new Map(rows.map(r=>[r.market+':'+r.code,r])),result=new Map();
 let total=0,joint=0,earnings=0;
 for(const g of dual.groups){
  if(!g)return null;
  const key=g.market+':'+g.current_dart_industry_prefix_2,r=byKey.get(key),n=g.verified_two_year_operating_profit_issuers,b=g.profit_improving_breadth_all_lower_bound;
  if(!r||result.has(key)||g.current_listed_issuers!==r.listed||g.verified_relative20_60_sample_issuers!==r.sample20_60||
   g.market_relative20_median_sample_pct!==r.m20||g.market_relative60_median_sample_pct!==r.m60||
   !Number.isInteger(n)||n<0||n>r.listed||!num(g.profit_pair_coverage)||Math.abs(g.profit_pair_coverage-n/r.listed)>.000002||
   !num(g.price_pair_coverage)||Math.abs(g.price_pair_coverage-r.sample20_60/r.listed)>.000002||
   (n<3?b!==null:!num(b)||b<0||b>n/r.listed+.000002||Math.abs(b*r.listed-Math.round(b*r.listed))>r.listed*.000002)||
   g.industry15_points!==null||g.investment_buy_signal!==false||g.historical_sector_PIT_verified!==false)return null;
  const price=r.state==='early_relative_recovery_watch',profit=r.listed>=10&&n/r.listed>=.75&&b!==null&&b>=.5;
  const state=price?(profit?'dual_evidence_watch':'price_only_watch'):(profit?'earnings_only_watch':'not_dual_evidence_watch');
  if(g.observed_state!==state)return null;
  result.set(key,{profitSample:n,profitLower:b,dualState:state});total+=n;joint+=Number(price&&profit);earnings+=Number(profit);
 }
 if(total!==dual.two_year_profit_verified_samples||joint!==dual.dual_evidence_watch_groups)return null;
 return {byKey:result,total,joint,earnings};
}
const MARKETS=['KOSPI','KOSDAQ'];
function marketRegime(values){
 const r=[20,60,120].map(n=>values?.[n]);
 if(!r.every(x=>num(x)&&x> -100))return '자료 부족';
 const [a,b,c]=r;
 if(a>0&&b>0&&c>0)return '상승 추세 지속';
 if(a>0&&b<0&&c<0)return '단기 반등·회복 관찰';
 if(a<0&&b>0&&c>0)return '상승 중 조정';
 if(a<0&&b<0&&c<0)return '하락 추세 지속';
 return '혼조·전환 관찰';
}
function indexEvidence(latest,ref){
 if(!latest||ref?.schema!=='v32-market-index-reference-1'||ref.asof!==latest||
 ref.source!=='FinanceDataReader KS11/KQ11 (secondary index reference)'||
 ref.official_krx_index_certified!==false||ref.new_provider_calls!==0||
 !ref.markets||typeof ref.markets!=='object'||Array.isArray(ref.markets)||
 Object.keys(ref.markets).some(k=>!MARKETS.includes(k)))return [];
 return MARKETS.flatMap(m=>{
  const r=ref.markets[m],v=r?.return_pct;
  if(r?.asof!==latest||!v||!['20','60','120'].every(n=>v[n]===null||(num(v[n])&&v[n]>-100))||
  !['20','60','120'].some(n=>num(v[n])))return [];
  return [{market:m,asof:latest,values:v,regime:marketRegime(v)}];
 });
}
function flowEvidence(latest,listed,coverage){
 if(!latest||coverage?.schema!=='v22-full-market-accounting-0.1'||coverage.asof!==latest||
 coverage.market_listing_size!==listed||coverage.foreign_institution_250_day_history_verified!==false||
 coverage.private_or_vendor_raw_copied!==false||
 !Number.isInteger(coverage.flow_confirmed_count)||coverage.flow_confirmed_count<0||
 coverage.flow_confirmed_count>listed||!Number.isInteger(coverage.flow_unavailable_in_errors_count)||
 coverage.flow_unavailable_in_errors_count<0||coverage.flow_unavailable_in_errors_count>listed)return null;
 return {asof:latest,listed,legacyConfirmed:coverage.flow_confirmed_count,unavailable:coverage.flow_unavailable_in_errors_count};
}
function view(summary,raw,official,dual,indexRef,coverage){
 const latest=day(summary?.asof)&&Number.isInteger(summary?.listed_total)&&summary.listed_total>0?summary.asof:null;
 const krx=official?.krx;
 const indexStatus=latest&&day(krx?.asof)&&krx.asof<=latest&&krx.status==='observed'?'공식 지수 원천 응답 있음 · 지수 식별 및 시계열 검증 전 ('+krx.asof+')':'공식 코스피·코스닥 지수 확인 대기';
 const index=indexEvidence(latest,indexRef),flow=flowEvidence(latest,summary?.listed_total,coverage);
 const empty={latest,indexStatus,index,flow,sector:null,profit:null,rows:[]};
 if(!latest||!day(raw?.asof)||raw.asof>latest||(raw.asof===latest&&raw.listed_total!==summary.listed_total))return empty;
 const validated=core?.industryPriceEvidenceView(raw,raw.asof,raw.listed_total);
 if(!validated)return empty;
 const rows=raw.groups.map(g=>{
  const code=g.current_dart_industry_prefix_2;
  const r=validated.byKey.get(g.market+':'+code);
  return {...r,name:core.KSIC2_NAMES[code]||'업종코드 '+code,state:g.early_rotation_research_state,
    lower:g.positive_relative20_breadth_all_lower_bound,upper:g.positive_relative20_breadth_all_upper_bound,
    eligible:r.sample20_60>=10&&r.sample20_60/r.listed>=.75};
 });
 const p=profitEvidence(raw,dual,rows);
 return {latest,indexStatus,index,flow,sector:{asof:raw.asof,stale:raw.asof<latest,paired:validated.paired,listed:raw.listed_total,watch:validated.watch},
 profit:p?{total:p.total,joint:p.joint,earnings:p.earnings}:null,
 rows:p?rows.map(r=>({...r,...p.byKey.get(r.market+':'+r.code)})):rows};
}
function selectRows(v,market='all',period='m20',watch='all'){
 if(!['m20','m60','m120'].includes(period)||!['all','KOSPI','KOSDAQ'].includes(market)||!['all','recovery','eligible','dual','earnings'].includes(watch))return [];
 return v.rows.filter(r=>(market==='all'||r.market===market)&&(watch==='all'||(watch==='eligible'?r.eligible:watch==='dual'?r.dualState==='dual_evidence_watch':watch==='earnings'?['dual_evidence_watch','earnings_only_watch'].includes(r.dualState):r.state==='early_relative_recovery_watch'))).sort((a,b)=>(num(b[period])?b[period]:-Infinity)-(num(a[period])?a[period]:-Infinity)||b.sample20_60-a.sample20_60||a.market.localeCompare(b.market)||a.code.localeCompare(b.code));
}
if(typeof module!=='undefined'&&module.exports)module.exports={view,selectRows,marketRegime,indexEvidence,flowEvidence};
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
const strength=v=>num(v)?(v>0?'+':'')+v.toFixed(2):'—';
const pct=v=>num(v)?(v*100).toFixed(0)+'%':'—';
function render(v){
 $('marketDate').textContent=v.latest?'최근 시장 자료 기준일 '+v.latest:'시장 기준일 미확인';
 $('indexStatus').textContent=v.indexStatus;
 $('indexRows').replaceChildren();
 for(const market of MARKETS){
  const r=v.index.find(x=>x.market===market),tr=D.createElement('tr');
  [market,r?.asof||'—',...['20','60','120'].map(n=>r&&num(r.values[n])?strength(r.values[n])+'%':'—'),r?.regime||'자료 부족'].forEach((s,i)=>{const td=D.createElement(i===0?'th':'td');if(i===0)td.scope='row';td.textContent=s;tr.append(td);});
  $('indexRows').append(tr);
 }
 $('flowStatus').textContent=v.flow?'기준일 '+v.flow.asof+' · 기존 수급 확인 표본 '+v.flow.legacyConfirmed+' / '+v.flow.listed+'종목 (독립 공식 검증 전) · 수급 오류 기록 '+v.flow.unavailable+'건':'동일 기준일 수급 확보 현황 미확인';
 $('flowValues').textContent='공식 기관·외국인 순매수 금액·수량 및 20/60/120/250일 누적: 미확보. 확인 표본 0은 순매수 0을 뜻하지 않습니다.';
 $('sectorStatus').textContent=v.sector?'업종 기준일 '+v.sector.asof+(v.sector.stale?' · 이전 거래일 자료 (최신 갱신 대기)':' · 최근 시장 자료와 동일')+' · 상대강도 표본 '+v.sector.paired+' / '+v.sector.listed+'종목 · 회복 관찰 '+v.sector.watch+'그룹':'업종 자료 미확인 또는 기준일·표본 불일치';
 $('profitStatus').textContent=v.profit?'FY2024~25 영업이익 비교 '+v.profit.total+' / '+v.sector.listed+'종목 · 실적 확산 '+v.profit.earnings+'그룹 · 가격·실적 동시 회복 '+v.profit.joint+'그룹':'실적 비교 자료 미확보 또는 가격 자료와 기준 불일치';
 const rows=selectRows(v,$('market').value,$('period').value,$('watch').value);
 $('groupCount').textContent=rows.length+'개 시장별 업종 그룹';
 $('groups').replaceChildren();
 for(const r of rows){
  const tr=D.createElement('tr');
  const values=[r.market+' · '+r.name,strength(r.m20),strength(r.m60),strength(r.m120),r.sample20_60+' / '+r.listed,r.sample120+' / '+r.listed,num(r.lower)&&num(r.upper)?pct(r.lower)+' ~ '+pct(r.upper):'—',states[r.state],Number.isInteger(r.profitSample)?r.profitSample+' / '+r.listed:'—',pct(r.profitLower),dualLabels[r.dualState]||'미확인'];
  values.forEach((v,i)=>{const td=D.createElement(i===0?'th':'td');if(i===0)td.scope='row';td.textContent=v;tr.append(td);});$('groups').append(tr);
 }
}
Promise.all(['v3-current-observed-all-listed-summary.json','v3-industry-observed-relative-strength.json','official.json','v3-industry-dual-evidence-watch.json','v32-market-index-reference.json','v22-coverage.json'].map(async path=>{try{const response=await root.fetch(path,{cache:'no-cache'});return response.ok?await response.json():null;}catch{return null;}})).then(([summary,raw,official,dual,indexRef,coverage])=>{
 const v=view(summary,raw,official,dual,indexRef,coverage);
 for(const id of ['market','period','watch'])$(id).addEventListener('change',()=>render(v));
 render(v);
});
})(typeof window!=='undefined'?window:globalThis);
