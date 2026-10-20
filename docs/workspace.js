/* Unified single-surface Korean-stock research. Source-aware, no fabricated V3 totals. */
(function(root){
'use strict';
const VERSION='v3-unified-research-workspace-0.1';
const WEIGHTS=Object.freeze({ias:20,technical:25,financial:15,valuation:15,industry:15,momentum:10});
const PILLARS=Object.freeze([['ias','수급'],['technical','기술적 분석'],['financial','재무'],['valuation','밸류에이션'],['industry','산업·섹터'],['momentum','모멘텀']]);
const finite=x=>typeof x==='number'&&Number.isFinite(x);
const esc=x=>String(x===null||x===undefined?'':x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
const won=x=>finite(x)?Math.round(x).toLocaleString('ko-KR')+'원':'미확인';
const pct=x=>finite(x)?(x>0?'+':'')+x.toFixed(1)+'%':'미확인';
const eok=x=>finite(x)?(x/1e8).toLocaleString('ko-KR',{maximumFractionDigits:1})+'억':'미확인';
const TODAY_CODE=/^[0-9]{6}$/;
const datePattern=/^\d{4}-\d{2}-\d{2}$/;
function validDate(v){
 if(typeof v!=='string'||!datePattern.test(v))return false;
 const parsed=new Date(v+'T00:00:00Z');
 return !Number.isNaN(parsed.getTime())&&parsed.toISOString().slice(0,10)===v;
}
function marketFreshness(asof,now=new Date()){
 if(!validDate(asof)||!(now instanceof Date)||!Number.isFinite(now.getTime()))
   return {status:'invalid',calendarDays:null};
 const dateParts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',
   year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
 const field=name=>dateParts.find(x=>x.type===name)?.value;
 const local=field('year')+'-'+field('month')+'-'+field('day');
 const days=Math.round((Date.parse(local+'T00:00:00Z')-
   Date.parse(asof+'T00:00:00Z'))/86400000);
 return {status:days<0?'future':days>5?'stale':'current',calendarDays:days};
}
function validPattern(r,kind){
 const p=r?.[kind==='weekly'?'pattern_weekly_research':'pattern_research'];
 if(!p||p.schema!=='v3-pattern-first-daily-research-0.1'||
   p.status!=='heuristic_unverified'||p.timeframe!==kind||
   p.buy_signal!==false||p.scoring_allowed!==false||
   p.official_regular_session_verified!==false||
   !Array.isArray(p.patterns)||p.patterns.length>6||
   !validDate(p.asof)||!validDate(r.price_date)||
   typeof p.rsi30_cross_recent!=='boolean'||
   typeof p.rsi_bullish_divergence_recent!=='boolean')return null;
 if(p.rsi14!==null&&(!finite(p.rsi14)||p.rsi14<0||p.rsi14>100))return null;
 if(kind==='daily'){if(p.asof!==r.price_date)return null;}
 else{
   const a=Date.parse(p.asof+'T00:00:00Z'), b=Date.parse(r.price_date+'T00:00:00Z');
   if(p.completed_week_only!==true||new Date(a).getUTCDay()!==5||
      a>b||b-a>13*86400000)return null;
 }
 const seen=new Set();
 for(const k of p.patterns){
   if(!k||!['bull_flag','double_bottom'].includes(k.type)||
     !['forming','breakout_observed'].includes(k.stage)||seen.has(k.type))return null;
   seen.add(k.type);
 }
 return p;
}
const names={bull_flag:'Bull Flag',double_bottom:'더블바텀',
 falling_wedge_candidate:'폴링 웻지',
 inverse_head_shoulders_breakout_candidate:'역헤드앤숄더',
 triangle_candidate:'삼각수렴',post_rise_consolidation_candidate:'상승 후 수렴'};
function evidence(r,kind){
 const data=validPattern(r,kind),out=[];
 if(data){
  for(const p of data.patterns)out.push({label:names[p.type],
   stage:p.stage==='breakout_observed'?'돌파 관측':'형성 중',
   timeframe:kind,kind:'pattern'});
  const geometry=data.pattern_geometry;
  if(geometry&&geometry.status==='heuristic_unvalidated')
   for(const name of ['falling_wedge_candidate','inverse_head_shoulders_breakout_candidate',
        'triangle_candidate','post_rise_consolidation_candidate']){
     if(geometry[name]===true)out.push({label:names[name],stage:'형상 후보',
       timeframe:kind,kind:'pattern'});
   }
  if(data.rsi30_cross_recent)out.push({label:'RSI 30 재돌파',stage:'최근 관측',
     timeframe:kind,kind:'rsi'});
  if(data.rsi_bullish_divergence_recent)out.push({label:'RSI 상승다이버전스',stage:'확정 피벗',
     timeframe:kind,kind:'rsi'});
 }
 if(kind==='daily'){
  // Clearly separate the older approximate observations from new candidates.
  if(!data&&Array.isArray(r.patterns))
   for(const title of r.patterns.slice(0,3)){
    if(typeof title==='string'&&/쐐기|역헤드|삼각|수렴|횡보|컵|더블바텀/.test(title))
     out.push({label:title,stage:'기존 패턴 근사',timeframe:'daily',kind:'pattern'});
   }
 }
 return out;
}
const TECH_PARTS=Object.freeze([
 ['daily_pattern',7],['weekly_pattern',8],['daily_rsi',2],
 ['weekly_rsi',3],['weekly_ma120',3],['volume_confirmation',2]
]);
function technicalEvidence(r){
 const z=r?.v3_pattern_integrated;
 if(!z||z.schema!=='v3-pattern-within-six-pillars-research-0.4'||
    !validDate(r.price_date)||z.asof!==r.price_date||
    z.technical_maximum!==25||z.buy_certified!==false||
    z.v3_release_ready!==false||z.investable_score!==null)return null;
 if(z.technical_weights&&TECH_PARTS.some(([k,w])=>z.technical_weights[k]!==w))return null;
 const full=z.technical_research_candidate;
 if(full!==null&&full!==undefined&&(!finite(full)||full<0||full>25))return null;
 const parts=z.technical_parts;
 if(parts&&typeof parts==='object'&&!Array.isArray(parts)){
  let earned=0,available=0;
  for(const [k,max] of TECH_PARTS){
   if(!Object.prototype.hasOwnProperty.call(parts,k))return null;
   const value=parts[k];
   if(value===null)continue;
   if(!finite(value)||value<0||value>max)return null;
   earned+=value;available+=max;
  }
  if(z.technical_observed_maximum!==undefined&&
     z.technical_observed_maximum!==available)return null;
  if(full!==null&&full!==undefined&&
     (available!==25||Math.abs(earned-full)>0.01))return null;
  return available>0?{earned:Math.round(earned*10000)/10000,
   available,fullyObserved:available===25}:null;
 }
 return finite(full)?{earned:full,available:25,fullyObserved:true}:null;
}
// Returns the full 25-point technical candidate only; partial parts are
// available separately for the user's unverified, inspectable scorecard.
function technicalCandidate(r){
 const x=technicalEvidence(r);
 return x&&x.fullyObserved?x.earned:null;
}
function momentumEvidence(r){
 const z=r?.v3_momentum_research;
 if(!z||z.schema!=='v3-observed-market-momentum-8of10-research-0.1'||
    z.asof!==r.price_date||!validDate(r.price_date)||
    z.market!==r.market||z.momentum_maximum!==10||
    z.buy_certified!==false||z.investable_score!==null||
    z.market_benchmark_date_independently_verified!==false||
    z.source_unadjusted_corporate_events_verified!==false||
    z.positive_days20_observed!==false||
    !Number.isInteger(z.peer20_count)||!Number.isInteger(z.peer60_count))return null;
 const parts=z.momentum_parts;
 if(!parts||typeof parts!=='object'||Array.isArray(parts)||
    parts.positive_days20!==null)return null;
 let total=0,available=0;
 for(const [name,max,peer] of [['relative20',4,z.peer20_count],
                                ['relative60',4,z.peer60_count]]){
  const value=parts[name];
  if(value===null)continue;
  if(!finite(value)||value<0||value>max||peer<80)return null;
  total+=value;available+=max;
 }
 if(!available||available!==z.momentum_observed_maximum||
    !finite(z.momentum_research_candidate)||
    Math.abs(total-z.momentum_research_candidate)>0.001)return null;
 return {earned:Math.round(total*10000)/10000,available};
}
function pillarState(r){
 const z=r?.v3_pattern_integrated;
 const tech=technicalEvidence(r);
 const momentum=momentumEvidence(r);
 const valid=z&&z.schema==='v3-pattern-within-six-pillars-research-0.4'&&
   z.asof===r.price_date&&validDate(r.price_date)&&
   z.buy_certified===false&&z.v3_release_ready===false&&
   z.investable_score===null;
 const supplied=valid&&z.six_pillar_candidates&&
   typeof z.six_pillar_candidates==='object'&&!Array.isArray(z.six_pillar_candidates)?
   z.six_pillar_candidates:null;
 const maxima=valid&&z.six_pillar_maximum;
 const matchingMaxima=!maxima||PILLARS.every(([key])=>maxima[key]===WEIGHTS[key]);
 return PILLARS.map(([key,label])=>{
  const maximum=WEIGHTS[key];
  let candidate=null,available=0;
  if(key==='technical'&&tech){candidate=tech.earned;available=tech.available;}
  else if(key==='momentum'&&momentum){
   candidate=momentum.earned;available=momentum.available;
  }
  else if(key!=='technical'&&key!=='momentum'&&supplied&&matchingMaxima){
   const value=supplied[key];
   // Independently supplied V3 research candidates only; never legacy factors.
   if(finite(value)&&value>=0&&value<=maximum){
    candidate=value;available=maximum;
   }
  }
  return {key,label,max:maximum,candidate,available,
   status:available>0?'미검증 연구점수':'자료 미확보'};
 });
}
function researchComposite(r){
 const pillars=pillarState(r);
 const available=pillars.reduce((n,p)=>n+p.available,0);
 const earned=pillars.reduce((n,p)=>n+(p.candidate===null?0:p.candidate),0);
 return {earned:available>0?Math.round(earned*10000)/10000:null,
  available,missing:100-available,asof:r.price_date,
  observedPillars:pillars.filter(p=>p.available>0).length,
  complete:available===100,provisional:true,pillars};
}
function riskState(x,core){
 const r=x.r;
 const distance=core.stopDistance(r);
 const reasons=Array.isArray(r.risk_flags)?r.risk_flags.filter(z=>typeof z==='string'):[];
 const hot=core.highRisk(r);
 const unsafe=hot||distance===null||distance<3||distance>15;
 return {unsafe,distance,description:hot?'과열·유동성·추세 위험':
   distance===null?'구조 참고선 미확인':
   distance<3?'손절선 과도하게 근접':distance>15?'손절 위험 과대':'손실 경계 검토 가능',
   reasons};
}
// Do not rank using V2.2 legacy points. Research-only ordering prioritizes
// observed V3 coverage, then observed point sum; unlike a real 100-point
// investment ranking, comparisons across different coverage are not final.
function compareResearchScores(a,b){
 const ac=researchComposite(a.r),bc=researchComposite(b.r);
 return bc.available-ac.available||
   (bc.earned??-1)-(ac.earned??-1)||
   String(a.r?.code||'').localeCompare(String(b.r?.code||''));
}
function rankItems(model,core,tab='discover',market='all',query='',sort='score',safeOnly=false,top100Only=false){
 if(!model?.rows||!core)return [];
 const ordered=model.rows.slice().sort(compareResearchScores);
 const candidatePositions=new Map();
 let next=0;
 for(const x of ordered)
  if(researchComposite(x.r).earned!==null)candidatePositions.set(x.r.code,++next);
 const search=String(query).trim().toLocaleLowerCase('ko-KR');
 const rows=model.rows.map(x=>{
  const daily=evidence(x.r,'daily'),weekly=evidence(x.r,'weekly');
  const signals=[...weekly,...daily];
  const risk=riskState(x,core);
  const patterns=signals.filter(z=>z.kind==='pattern');
  const rsi=signals.filter(z=>z.kind==='rsi');
  const composite=researchComposite(x.r);
  const tech=technicalCandidate(x.r);
  const priority=signals.reduce((acc,z)=>acc+(z.kind==='pattern'?
    (z.timeframe==='weekly'?4:3)+(z.stage==='돌파 관측'?1:0):1),0);
  return {...x,daily,weekly,signals,patterns,rsi,risk,tech,priority,composite,
    candidatePosition:candidatePositions.get(x.r.code)??null};
 }).filter(x=>{
  const r=x.r;
  // Direct ticker lookup is always global. Never suppress a ticker because
  // its provisional research score is missing or an optional tab is selected.
  if(search)return (r.name+' '+r.code+' '+(r.sector||''))
    .toLocaleLowerCase('ko-KR').includes(search);
  if(top100Only&&(x.candidatePosition===null||x.candidatePosition>100))return false;
  if(market!=='all'&&r.market!==market)return false;
  if(safeOnly&&x.risk.unsafe)return false;
  if(tab==='patterns')return x.patterns.length>0;
  if(tab==='rsi')return x.rsi.length>0;
  if(tab==='quant')return x.quant;
  if(tab==='discover')return x.patterns.length>0||x.rsi.length>0||
    (x.watch!==null&&x.watch!==undefined);
  return true;
 });
 rows.sort((a,b)=>{
  if(sort==='score')return compareResearchScores(a,b);
  if(sort==='risk')return Number(a.risk.unsafe)-Number(b.risk.unsafe)||
   (a.risk.distance??1e4)-(b.risk.distance??1e4)||
   compareResearchScores(a,b);
  return Number(a.risk.unsafe)-Number(b.risk.unsafe)||
   b.priority-a.priority||compareResearchScores(a,b);
 });
 return rows;
}
function sampleQuant(x,core){
 if(!x.q)return '동일 기준일 퀀트 연구 없음';
 return core.quantGlance(x.q);
}
function technicalParts(r){
 const evidence=technicalEvidence(r),source=r?.v3_pattern_integrated?.technical_parts;
 if(!evidence||!source||typeof source!=='object')return '';
 const items=[['daily_pattern','일봉 패턴',7],['weekly_pattern','주봉 패턴',8],
  ['daily_rsi','일봉 RSI',2],['weekly_rsi','주봉 RSI',3],
  ['weekly_ma120','120주선',3],['volume_confirmation','거래량',2]];
 return '<div class="u-parts">'+items.map(([k,label,max])=>
  '<div class="'+(finite(source[k])?'u-factor-unverified':'u-legacy-unverified')+'"><span>'+
   label+'</span><b>'+(finite(source[k])?source[k].toFixed(1):'미산출')+
   ' / '+max+'</b></div>').join('')+'</div>';
}
function quantTable(x,core){
 if(!x.q)return '';
 const periods=['스윙 1개월','스윙 2개월','중기 3개월','중기 6개월','장기 1년'];
 const found=periods.map(name=>[name,core.period(x.q,name)]).filter(a=>a[1]);
 if(!found.length)return '';
 return '<div class="u-scroller"><table><thead><tr><th>기간</th><th>표본</th>'+
  '<th>평균 수익</th><th>손절 먼저</th></tr></thead><tbody>'+
  found.map(([name,z])=>'<tr><th>'+esc(name)+'</th><td>'+z.sample_n+
   '건</td><td>'+pct(z.mean_net_return_pct)+'</td><td>'+
   pct(z.stop_first_pct)+'</td></tr>').join('')+'</tbody></table></div>';
}
function weeklyCoverageText(report, marketDate){
 if(!report||report.schema!=='v3-seven-day-research-coverage-0.1'||
    !validDate(report.market_asof)||!validDate(report.run_kst)||
    !validDate(report.cycle_start)||!validDate(marketDate)||
    report.market_asof>marketDate||
    typeof report.listed_total!=='number'||report.listed_total<1||
    !Number.isInteger(report.processed_this_cycle)||
    !Number.isInteger(report.unprocessed_this_cycle)||
    report.processed_this_cycle<0||report.unprocessed_this_cycle<0||
    report.processed_this_cycle+report.unprocessed_this_cycle!==report.listed_total||
    !Array.isArray(report.cohort_counts)||report.cohort_counts.length!==7||
    report.cohort_counts.some(n=>!Number.isInteger(n)||n<0)||
    report.cohort_counts.reduce((a,b)=>a+b,0)!==report.listed_total||
    report.source_collection_calls!==0||
    report.verified_full_six_pillar_issuer_count!==0)return '';
 const data=report.source_reference_counts_this_cycle;
 const p=data&&Number.isInteger(data.technical_parts)&&data.technical_parts>=0&&
  data.technical_parts<=report.processed_this_cycle?data.technical_parts:null;
 return ' · V3 주간 순환 연구 '+report.processed_this_cycle+'/'+
  report.listed_total+'종목 · 자료기준 '+report.market_asof+
  (p===null?'':' · 기술점수 근거 '+p+'종목')+
  ' (기존 원본 재계산, 별도 실시간 API 수집 아님)';
}
function applyRotationTechnicalBackfill(model, state){
 if(!model||!Array.isArray(model.rows)||!state||
    state.schema!=='v3-seven-day-research-source-rotation-0.1'||
    !validDate(model.date)||state.market_asof!==model.date||
    !validDate(state.run_kst)||!validDate(state.cycle_start)||
    state.run_kst<state.cycle_start||
    !state.records||typeof state.records!=='object'||
    Array.isArray(state.records)||Object.keys(state.records).length>6000||
    state.source_collection_calls!==0||state.legacy_v22_score_used!==false||
    state.v3_live_full_six_pillar_score_claimed!==false)return 0;
 let accepted=0;
 for(const x of model.rows){
  const r=x.r,rec=state.records[r.code];
  if(!rec||rec.code!==r.code||rec.market_source_asof!==model.date||
     r.price_date!==model.date||!validDate(rec.last_recalculated_kst)||
     rec.last_recalculated_kst>state.run_kst||
     rec.six_pillar_research_score_confirmed!==false)continue;
  const mom=rec.momentum_research_reference;
  if(r.v3_momentum_research===undefined&&mom&&
     finite(rec.momentum_research_points)&&
     momentumEvidence({...r,v3_momentum_research:mom})?.earned===
       Math.round(rec.momentum_research_points*10000)/10000 &&
     momentumEvidence({...r,v3_momentum_research:mom})?.available===
       rec.momentum_available_weight){
    r.v3_momentum_research=mom;
    accepted++;
  }
  if(r.v3_pattern_integrated!==null&&r.v3_pattern_integrated!==undefined)continue;
  if(rec.technical_rebuilt_from_saved_source!==true||
     !rec.technical_research_backfill)continue;
  const shadow={...r,v3_pattern_integrated:rec.technical_research_backfill};
  const observed=technicalEvidence(shadow);
  if(!observed||!finite(rec.research_score_points)||
     rec.available_weight!==observed.available||
     Math.abs(rec.research_score_points-observed.earned)>0.01)continue;
  r.v3_pattern_integrated=rec.technical_research_backfill;
  accepted++;
 }
 return accepted;
}
function summary(model,core){
 const discover=rankItems(model,core,'discover');
 return {date:model.date,total:model.rows.length,eligible:model.marketEligible,
  discover:discover.length,pattern:rankItems(model,core,'patterns').length,
  rsi:rankItems(model,core,'rsi').length,
  technical:model.rows.filter(x=>technicalEvidence(x.r)!==null).length,
  provisional:model.rows.filter(x=>researchComposite(x.r).earned!==null).length,
  officialFullScore:0,rollingBook:model.rollingBookCount,quant:model.quantCount};
}
const api={VERSION,WEIGHTS,PILLARS,esc,won,pct,eok,validDate,validPattern,evidence,
 technicalEvidence,momentumEvidence,technicalCandidate,pillarState,researchComposite,riskState,rankItems,compareResearchScores,
 sampleQuant,summary,marketFreshness,weeklyCoverageText,applyRotationTechnicalBackfill};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
root.V3UnifiedCore=api;
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
if(!$('unifiedApp'))return;
let weeklyCoverage=null;
let model=null,core=null,tab='discover',sort='score',safeOnly=false,top100Only=false,selected=null,limit=20;
function label(item){return item.stage==='형상 후보'?'형상 후보':item.stage;}
function patternPill(item){
 const weekly=item.timeframe==='weekly';
 return '<span class="u-signal '+(weekly?'week':'day')+'">'+
  (weekly?'주봉 ':'일봉 ')+esc(item.label)+' · '+esc(label(item))+'</span>';
}
function renderDetails(x){
 const view=$('detail');
 if(!x){view.innerHTML='';view.hidden=true;return;}
 const r=x.r;
 const pillars=pillarState(r);
 const annual=x.financialRef;
 const annualReference=core.simpleAnnualOfsBookReference(r,annual);
 const book=annualReference!==null?annualReference:x.rollingBook?.value??null;
 const yearly=annual?.values||{};
 const four=x.fourYearRef;
 const stockLink=core.stockPage(r.code);
 const risk=x.risk;
 const quant=sampleQuant(x,core);
 const composite=researchComposite(r);
 const part=pillars.map(i=>'<div class="u-pillar u-factor-unverified"><span>'+esc(i.label)+
  ' <small>/'+i.max+'</small></span><b>'+
  (i.candidate===null?'— · 자료 미확보':i.candidate.toFixed(1)+'점')+
  '<em class="u-factor-mark">'+(i.candidate===null?'미산출 · 미검증':
   '미검증 · 확보 '+i.available+'/'+i.max)+'</em></b></div>').join('');
 view.hidden=false;
 view.innerHTML='<div class="u-detail-top"><div><small>종목별 분석 · '+esc(model.date)+'</small>'+
 '<h2>'+esc(r.name)+'</h2><p>'+esc(r.code)+' · '+esc(r.market)+'</p></div>'+
 '<button id="detailClose" type="button" aria-label="분석 닫기">✕</button></div>'+
 '<div class="u-verdict '+(risk.unsafe?'warning':'neutral')+'"><strong>'+esc(risk.description)+'</strong>'+
 '<span>V3 잠정점수는 미검증 연구값이며 매수 신호가 아닙니다.</span></div>'+
 '<div class="u-detail-grid"><div><small>종가</small><strong>'+won(r.price)+'</strong></div>'+
 '<div><small>구조 무효화 참고선</small><strong>'+won(r.stop_line)+'</strong></div>'+
 '<div><small>참고 손절거리</small><strong>'+(risk.distance===null?'미확인':pct(-risk.distance))+'</strong></div>'+
 '<div class="u-factor-unverified"><small>V3 잠정 종합점수 (미검증)</small><strong>'+
 (composite.earned===null?'—':composite.earned.toFixed(1)+' / 100')+
 '</strong><small>산출 가능 배점 '+composite.available+'/100 · 기준 '+esc(composite.asof)+
 '</small></div></div>'+
 '<h3>잠정 합계 산출근거</h3>'+
 '<p class="u-muted">아래 연구점수 중 실제로 계산된 값만 더한 합계입니다. '+
 '미확보 영역을 0점으로 처리하거나 100점으로 환산하지 않습니다. '+
 '모든 점수는 새 관측 기준일마다 갱신되는 값이며 매수 추천이 아닙니다.</p>'+
 '<h3>일봉 · 스윙 관찰</h3>'+
 (x.daily.length?'<div class="u-signals">'+x.daily.map(patternPill).join('')+'</div>':
 '<p class="u-muted">확인된 선호 패턴·RSI 관측 없음 또는 당일 자료 미확보</p>')+
 '<h3>주봉 · 중기/장기 관찰</h3>'+
 (x.weekly.length?'<div class="u-signals">'+x.weekly.map(patternPill).join('')+'</div>':
 '<p class="u-muted">완성 주봉의 패턴·RSI 관측 미확인 (진행 중인 주봉 제외)</p>')+
 '<h3>V3 통합 100점 · 영역별 검증 상태</h3>'+
 '<p class="u-muted">V3 잠정 합계: <b>'+
 (composite.earned===null?'자료 미확보':composite.earned.toFixed(1)+' / 100')+
 ' · 미검증</b> · 산출 가능 '+composite.available+'/100 · 미확보 '+
 composite.missing+'/100. 현재 데이터 기준의 임시 연구값으로 매번 갱신합니다.</p>'+
 '<div class="u-pillars">'+part+'</div>'+
 '<p class="u-muted">기술적 분석 25점 안의 근거별 구성 (일봉/주봉 독립 평가)</p>'+
 technicalParts(r)+
 '<h3>재무 · 밸류 참고</h3>'+
 '<p class="u-muted">'+(annual?
  esc(annual.year)+'년 '+esc(annual.fs_div)+' 재무: 매출 '+eok(yearly.revenue)+
  ' / 영업이익 '+eok(yearly.operating_income)+
  ' / 영업현금흐름 '+eok(yearly.operating_cashflow):
  '동일 기준일에 검증 가능한 연간 공시 실적 미연결')+'</p>'+
 '<p class="u-muted">4개년 연간공시 연구 참고: '+(four?
  esc(four.years.join('~'))+'년 '+esc(four.scope)+' · 매출 3년 CAGR '+
  (finite(four.cagr)?four.cagr.toFixed(2)+'%':'미확보')+
  ' · 3년 평균 영업이익률 '+
  (finite(four.margin)?four.margin.toFixed(2)+'%':'미확보'):
  '미수집 또는 연도·회계범위 정합성 미충족')+
  ' · 공시 정정/PIT 검증 전으로 재무 15점 및 밸류 15점은 미산출.</p>'+
 '<p class="u-muted">비공식 간이 P/B '+(book!==null?book.toFixed(2)+'배':'미산출')+
 ' · 정식 PBR 15점과 별개. 기준일·회계범위 확인 필요.</p>'+
 '<h3>퀀트 · 과거 유사조건</h3><p class="u-muted">'+esc(quant)+'</p>'+
 quantTable(x,core)+
 '<h3>손실 위험 · 가설 무효화</h3><p class="u-muted">연구점수와 별도로 위험요인을 점검해야 합니다'+
 ' · '+esc(risk.reasons.join(' / ')||'추가 위험 경고 관측 없음')+
 '. 구조선 이탈 시 재검토. 실제 체결 손실은 더 클 수 있습니다.</p>'+
 '<div class="u-actions"><a href="'+esc(stockLink)+'" target="_blank" rel="noopener noreferrer">종목 차트 ↗</a>'+
 '<button type="button" id="backToList">목록으로</button></div>';
 $('detailClose').onclick=()=>{view.hidden=true;selected=null;draw();};
 $('backToList').onclick=()=>{$('stockList').scrollIntoView({behavior:'smooth',block:'start'});};
 if(root.matchMedia?.('(max-width: 860px)')?.matches)view.scrollIntoView({behavior:'smooth',block:'start'});
}
function draw(){
 if(!model)return;
 const query=$('stockSearch').value.trim();
 const rows=rankItems(model,core,tab,$('market').value,query,sort,safeOnly,top100Only);
 $('filterNotice').textContent=query?
   '전체 시장 검색 결과 · TOP100·선택 탭·위험 필터 제한 없음':
   '종목명 검색은 TOP100 제한 없이 전체에서 검색됩니다.';
 $('resultCount').textContent=rows.length.toLocaleString('ko-KR')+
  '종목 · '+(query?'전체 시장 검색 · ':top100Only?'V3 잠정관측 상위 100개 중 ':'전체 시장 중 ')+
  (sort==='score'?'V3 잠정점수 관측범위 우선':sort==='risk'?'손절 위험순':'패턴 관찰순')+
  ' · 점수 미검증 · 관측범위가 다른 종목 간 직접 순위 비교 주의';
 $('stockList').innerHTML=rows.length?rows.slice(0,limit).map((x,i)=>{
  const r=x.r;
  const tags=x.signals.slice(0,3).map(patternPill).join('');
  const state=x.risk.unsafe?'위험 주의':x.risk.distance===null?'손절선 미확인':'구조적 위험 검토';
  return '<button class="u-stock '+(selected===r.code?'selected':'')+
   '" type="button" data-stock="'+esc(r.code)+'" aria-label="'+esc(r.name)+' 종목 상세 분석 열기">'+
   '<div class="u-stock-name"><span class="u-ranked"><b class="u-rank-number" title="정식 투자 순위가 아닌 V3 연구 상태">'+
   (x.composite.earned===null?'대기':'연구')+
   '</b><strong>'+esc(r.name)+'</strong></span><small>'+
   esc(r.code)+' · '+esc(r.market)+'</small></div>'+
   '<div class="u-stock-main"><span><b>'+won(r.price)+'</b><small>종가</small></span>'+
   '<span class="u-provisional-total"><b>'+
   (x.composite.earned===null?'—':x.composite.earned.toFixed(1))+
   '</b><small>V3 잠정 합계 · 미검증</small><small>산출 가능 '+
   x.composite.available+'/100</small></span></div>'+
   '<div class="u-scoresix" aria-label="V3 잠정점수: 빨간 테두리는 미검증 표기">'+x.composite.pillars.map(i=>
     '<span class="u-factor-unverified"><small>'+esc(i.label)+' /'+i.max+
     '</small><b>'+(i.candidate===null?'—':i.candidate.toFixed(1))+
     '</b><em class="u-factor-mark">'+(i.available===0?'자료없음 · 미검증':
      '미검증')+'</em></span>').join('')+
     '<span class="u-factor-unverified u-total-factor"><small>잠정 합계 /100</small><b>'+
     (x.composite.earned===null?'—':x.composite.earned.toFixed(1))+
     '</b><em class="u-factor-mark">미검증</em></span></div>'+
   '<p class="u-score-hint">잠정 합계 = 실제 계산된 V3 연구점수 합계 · 산출배점 '+
   x.composite.available+'/100 · 누락항목은 0점 처리하지 않음 · '+
   '서로 다른 확보 배점은 직접 비교할 수 없습니다.</p>'+
   '<div class="u-tags">'+(tags||'<span class="u-faint">일·주봉 신호 미확인</span>')+'</div>'+
   '<div class="u-stock-foot"><span class="'+(x.risk.unsafe?'u-danger':'u-caution')+'">'+esc(state)+
   '</span><span>'+ (x.tech===null?'V3 기술점수 미산출':'V3 기술 연구 '+x.tech.toFixed(1)+'/25')+'</span>'+
   '<span>손절 참고 '+(x.risk.distance===null?'미확인':x.risk.distance.toFixed(1)+'%')+'</span></div></button>';
 }).join(''):'<div class="u-empty">해당 조건을 만족하는 종목이 없습니다. 후보를 임의로 만들지 않습니다. 필터를 변경해 확인할 수 있어요.</div>';
 $('more').hidden=rows.length<=limit;
 $('top100Only').setAttribute('aria-pressed',String(top100Only));
 if(selected){
  const x=rows.find(y=>y.r.code===selected)||
    rankItems(model,core,'all','all',selected,'score',false).find(y=>y.r.code===selected);
  if(x)renderDetails(x);
 }
}
function present(data){
 const metrics=summary(data,core);
 const freshness=marketFreshness(metrics.date);
 if(freshness.status==='invalid'||freshness.status==='future')
   throw Error('invalid_or_future_market_snapshot');
 $('state').textContent='자료 기준 '+metrics.date+
   (freshness.status==='stale'?
     ' · 5일 이상 지난 분석: 현재 투자판단에 사용 주의':
     ' · 실제 장마감 관측 (실시간 시세 아님)');
 $('state').className=freshness.status==='stale'?'u-state warning':'u-state';
 $('statAnalyzed').textContent=metrics.total.toLocaleString('ko-KR');
 $('statWatch').textContent=metrics.discover.toLocaleString('ko-KR');
 $('statTechnical').textContent=metrics.technical.toLocaleString('ko-KR');
 $('statV3').textContent=metrics.provisional.toLocaleString('ko-KR')+'종목';
 $('sourceNotes').textContent='전체 '+(metrics.eligible||'미확인')+
  '종목 중 가격 분석 '+metrics.total+'종목 · 일/주봉 관찰 후보 '+metrics.discover+
  '종목 · V3 기술 세부항목 점수 '+metrics.technical+
  '종목 · V3 잠정 합계 '+metrics.provisional+
  '종목. 실시간 투자등급이 아닌 관측 기준일별 미검증 점수입니다. '+
  '일부 데이터가 누락되어 있으면 합계가 낮게 표시될 수 있습니다. '+
  '4개년 재무 참고 '+data.rows.filter(x=>x.fourYearRef!==null).length+
  '종목 (재무 15점 확정 아님). 주간 순환 연구는 기존 원천 자료 재계산이며 6개 영역의 데이터 확보는 진행 중입니다.'+
  weeklyCoverageText(weeklyCoverage,model.date);
 draw();
}
async function load(){
 const s=$('state');s.className='u-state';s.textContent='장마감 분석 자료 확인 중…';
 const files=['data.json','quant.json','recommendations.json','official.json',
  'v3-priority-quant.json','v3-dart-book-reference.json','v3-weekly-coverage.json',
  'v3-weekly-rotation.json','v3-dart-four-year-reference.json'];
 try{
  const responses=await Promise.all(files.map(async file=>{
   try{const r=await root.fetch(file+'?v='+Date.now(),{cache:'no-store'});
    return r.ok?await r.json():null;}catch(_){return null;}
  }));
  core=root.V3RankedCore;
  if(!core)throw Error('missing_research_core');
  model=core.prepare(...responses.slice(0,6),responses[8]);
  weeklyCoverage=responses[6];
  applyRotationTechnicalBackfill(model,responses[7]);
  selected=null;$('detail').hidden=true;
  present(model);
 }catch(_){
  model=null;s.className='u-state error';
  s.textContent='현재 전 종목 관측자료를 검증할 수 없습니다. 잘못된 추천 대신 데이터를 보류합니다.';
  $('stockList').innerHTML='<div class="u-empty">기존 기록은 안전하게 유지되고 있습니다. 잠시 후 다시 확인해 주세요.</div>';
  $('resultCount').textContent='자료 검증 대기';
 }
}
function init(){
 $('tabs').addEventListener('click',e=>{
  const t=e.target.closest('[data-tab]');if(!t)return;
  tab=t.dataset.tab;limit=20;
  for(const b of $('tabs').querySelectorAll('[data-tab]'))
   b.setAttribute('aria-pressed',String(b===t));
  draw();
 });
 $('stockSearch').addEventListener('input',()=>{limit=20;draw();});
 $('market').addEventListener('change',()=>{limit=20;draw();});
 $('sort').addEventListener('change',e=>{sort=e.target.value;limit=20;draw();});
 $('saferOnly').addEventListener('change',e=>{safeOnly=e.target.checked;limit=20;draw();});
 $('reload').addEventListener('click',load);
 $('more').addEventListener('click',()=>{limit+=20;draw();});
 $('top100Only').addEventListener('click',()=>{
   top100Only=!top100Only;limit=20;draw();
 });
 $('stockList').addEventListener('click',e=>{
  const b=e.target.closest('[data-stock]');if(!b)return;
  selected=b.dataset.stock;draw();
 });
 load();
}
D.addEventListener('DOMContentLoaded',init);
})(typeof window!=='undefined'?window:globalThis);
