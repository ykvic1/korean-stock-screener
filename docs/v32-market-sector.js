(function(root){
'use strict';
const core=typeof module!=='undefined'&&module.exports?require('./workspace.js'):root.V3UnifiedCore;
const day=x=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&Number.isFinite(Date.parse(x+'T00:00:00Z'))&&new Date(x+'T00:00:00Z').toISOString().slice(0,10)===x;
const states={insufficient_sample:'표본 부족',early_relative_recovery_watch:'회복 관찰',relative20_extension_caution:'20일 과열 주의',no_early_recovery_pattern_observed:'회복 조건 미충족'};
const num=x=>typeof x==='number'&&Number.isFinite(x);
function view(summary,raw,official){
 const latest=day(summary?.asof)&&Number.isInteger(summary?.listed_total)&&summary.listed_total>0?summary.asof:null;
 const krx=official?.krx;
 const indexStatus=latest&&day(krx?.asof)&&krx.asof<=latest&&krx.status==='observed'?'공식 지수 원천 응답 있음 · 지수 식별 및 시계열 검증 전 ('+krx.asof+')':'공식 코스피·코스닥 지수 확인 대기';
 const empty={latest,indexStatus,sector:null,rows:[]};
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
 return {latest,indexStatus,sector:{asof:raw.asof,stale:raw.asof<latest,paired:validated.paired,listed:raw.listed_total,watch:validated.watch},rows};
}
function selectRows(v,market='all',period='m20',watch='all'){
 if(!['m20','m60','m120'].includes(period)||!['all','KOSPI','KOSDAQ'].includes(market)||!['all','recovery','eligible'].includes(watch))return [];
 return v.rows.filter(r=>(market==='all'||r.market===market)&&(watch==='all'||(watch==='eligible'?r.eligible:r.state==='early_relative_recovery_watch'))).sort((a,b)=>(num(b[period])?b[period]:-Infinity)-(num(a[period])?a[period]:-Infinity)||b.sample20_60-a.sample20_60||a.market.localeCompare(b.market)||a.code.localeCompare(b.code));
}
if(typeof module!=='undefined'&&module.exports)module.exports={view,selectRows};
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
const strength=v=>num(v)?(v>0?'+':'')+v.toFixed(2):'—';
const pct=v=>num(v)?(v*100).toFixed(0)+'%':'—';
function render(v){
 $('marketDate').textContent=v.latest?'최근 시장 자료 기준일 '+v.latest:'시장 기준일 미확인';
 $('indexStatus').textContent=v.indexStatus;
 $('sectorStatus').textContent=v.sector?'업종 기준일 '+v.sector.asof+(v.sector.stale?' · 이전 거래일 자료 (최신 갱신 대기)':' · 최근 시장 자료와 동일')+' · 상대강도 표본 '+v.sector.paired+' / '+v.sector.listed+'종목 · 회복 관찰 '+v.sector.watch+'그룹':'업종 자료 미확인 또는 기준일·표본 불일치';
 const rows=selectRows(v,$('market').value,$('period').value,$('watch').value);
 $('groupCount').textContent=rows.length+'개 시장별 업종 그룹';
 $('groups').replaceChildren();
 for(const r of rows){
  const tr=D.createElement('tr');
  const values=[r.market+' · '+r.name,strength(r.m20),strength(r.m60),strength(r.m120),r.sample20_60+' / '+r.listed,r.sample120+' / '+r.listed,num(r.lower)&&num(r.upper)?pct(r.lower)+' ~ '+pct(r.upper):'—',states[r.state]];
  values.forEach((v,i)=>{const td=D.createElement(i===0?'th':'td');if(i===0)td.scope='row';td.textContent=v;tr.append(td);});$('groups').append(tr);
 }
}
Promise.all(['v3-current-observed-all-listed-summary.json','v3-industry-observed-relative-strength.json','official.json'].map(async path=>{try{const response=await root.fetch(path,{cache:'no-cache'});return response.ok?await response.json():null;}catch{return null;}})).then(([summary,raw,official])=>{
 const v=view(summary,raw,official);
 for(const id of ['market','period','watch'])$(id).addEventListener('change',()=>render(v));
 render(v);
});
})(typeof window!=='undefined'?window:globalThis);
