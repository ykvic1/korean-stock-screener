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
function technicalCandidate(r){
 const z=r?.v3_pattern_integrated;
 if(!z||z.schema!=='v3-pattern-within-six-pillars-research-0.4'||
    z.asof!==r.price_date||z.technical_maximum!==25||
    z.buy_certified!==false||z.v3_release_ready!==false||
    z.investable_score!==null)return null;
 const score=z.technical_research_candidate;
 return finite(score)&&score>=0&&score<=25?score:null;
}
function pillarState(r){
 const z=r?.v3_pattern_integrated;
 const ok=z&&z.schema==='v3-pattern-within-six-pillars-research-0.4'&&
   z.asof===r.price_date&&z.investable_score===null&&z.buy_certified===false;
 return PILLARS.map(([key,label])=>{
  // Only date-valid technical evidence is available at this stage.
  // Do not present historical legacy v2.2 sub-scores as V3 pillar values.
  const candidate=key==='technical'?technicalCandidate(r):null;
  return {key,label,max:WEIGHTS[key],candidate,status:candidate===null?'미산출':'연구 관측'};
 });
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
// Global rank is a comparison of the score ACTUALLY shown on each card.
// It is the old v2.2 observed score (coverage separately disclosed), not
// the not-yet-verified V3 six-pillar investment total.
function observedDisplayScore(x){
 return finite(x.r?.score)?Math.round(x.r.score*10)/10:-1;
}
function compareObservedScores(a,b){
 const av=observedDisplayScore(a), bv=observedDisplayScore(b);
 const ac=finite(a.r?.coverage_pct)?a.r.coverage_pct:-1;
 const bc=finite(b.r?.coverage_pct)?b.r.coverage_pct:-1;
 return bv-av||bc-ac||String(a.r?.code||'').localeCompare(String(b.r?.code||''));
}
function rankItems(model,core,tab='discover',market='all',query='',sort='score',safeOnly=false,top100Only=false){
 if(!model?.rows||!core)return [];
 const globallyOrdered=model.rows.slice().sort(compareObservedScores);
 let previousScore=null,tieRank=0;
 const scoreRanks=new Map(globallyOrdered.map((x,i)=>{
   const score=observedDisplayScore(x);
   if(score!==previousScore){previousScore=score;tieRank=i+1;}
   return [x.r.code,tieRank];
 }));
 const search=String(query).trim().toLocaleLowerCase('ko-KR');
 // A direct ticker lookup is ALWAYS global, never restricted to TOP100,
 // tabs, market or safety filter. Risk warnings remain visible on cards.
 // Retain global legacy rank for comparability without fake V3 rank.
 const rows=model.rows.map(x=>{
  const daily=evidence(x.r,'daily'),weekly=evidence(x.r,'weekly');
  const signals=[...weekly,...daily];
  const risk=riskState(x,core);
  const patterns=signals.filter(z=>z.kind==='pattern');
  const rsi=signals.filter(z=>z.kind==='rsi');
  const tech=technicalCandidate(x.r);
  const priority=signals.reduce((acc,z)=>acc+(z.kind==='pattern'?
    (z.timeframe==='weekly'?4:3)+(z.stage==='돌파 관측'?1:0):1),0);
  return {...x,daily,weekly,signals,patterns,rsi,risk,tech,priority,
    scoreRank:scoreRanks.get(x.r.code)};
 }).filter(x=>{
  const r=x.r;
  if(search)return (r.name+' '+r.code+' '+(r.sector||''))
     .toLocaleLowerCase('ko-KR').includes(search);
  if(top100Only&&scoreRanks.get(r.code)>100)return false;
  if(market!=='all'&&r.market!==market)return false;
  if(safeOnly&&x.risk.unsafe)return false;
  if(tab==='patterns')return x.patterns.length>0;
  if(tab==='rsi')return x.rsi.length>0;
  if(tab==='quant')return x.quant;
  // A high score must never disappear silently. Display the explicit
  // downside warning; users can apply the separate safer-only filter.
  if(tab==='discover')return x.patterns.length>0||x.rsi.length>0||
    (x.watch!==null&&x.watch!==undefined);
  return true;
 });
 rows.sort((a,b)=>{
  if(sort==='score')return compareObservedScores(a,b);
  if(sort==='risk')return Number(a.risk.unsafe)-Number(b.risk.unsafe)||
     (a.risk.distance??1e4)-(b.risk.distance??1e4)||
     compareObservedScores(a,b);
  return Number(a.risk.unsafe)-Number(b.risk.unsafe)||
    b.priority-a.priority||
    Number(b.researchCandidate)-Number(a.researchCandidate)||
    compareObservedScores(a,b);
 });
 return rows;
}
function sampleQuant(x,core){
 if(!x.q)return '동일 기준일 퀀트 연구 없음';
 return core.quantGlance(x.q);
}
function technicalParts(r){
 const score=technicalCandidate(r),source=r?.v3_pattern_integrated?.technical_parts;
 if(score===null||!source||typeof source!=='object')return '';
 const items=[['daily_pattern','일봉 패턴',7],['weekly_pattern','주봉 패턴',8],
  ['daily_rsi','일봉 RSI',2],['weekly_rsi','주봉 RSI',3],
  ['weekly_ma120','120주선',3],['volume_confirmation','거래량',2]];
 if(items.some(([k,,max])=>source[k]!==null&&
    (!finite(source[k])||source[k]<0||source[k]>max)))return '';
 return '<div class="u-parts">'+items.map(([k,label,max])=>
  '<div><span>'+label+'</span><b>'+(finite(source[k])?source[k].toFixed(1):'미확인')+
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
function summary(model,core){
 const discover=rankItems(model,core,'discover');
 return {date:model.date,total:model.rows.length,eligible:model.marketEligible,
  discover:discover.length,pattern:rankItems(model,core,'patterns').length,
  rsi:rankItems(model,core,'rsi').length,
  technical:model.rows.filter(x=>technicalCandidate(x.r)!==null).length,
  officialFullScore:0,rollingBook:model.rollingBookCount,
  quant:model.quantCount};
}
const api={VERSION,WEIGHTS,PILLARS,esc,won,pct,eok,validDate,validPattern,evidence,
 technicalCandidate,pillarState,riskState,rankItems,compareObservedScores,observedDisplayScore,
 sampleQuant,summary,marketFreshness};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
root.V3UnifiedCore=api;
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
if(!$('unifiedApp'))return;
let model=null,core=null,tab='discover',sort='score',safeOnly=false,top100Only=true,selected=null,limit=20;
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
 const stockLink=core.stockPage(r.code);
 const risk=x.risk;
 const quant=sampleQuant(x,core);
 const part=pillars.map(i=>'<div class="u-pillar u-factor-unverified"><span>'+esc(i.label)+' <small>/'+i.max+'</small></span>'+
  '<b>'+(i.candidate===null?'미검증':i.candidate.toFixed(1)+'점 <em class="u-factor-mark">미검증</em>')+'</b></div>').join('');
 const legacyParts=Object.entries(model.weights||{}).map(([name,max])=>{
  const value=r.components?.[name];
  const calculated=finite(max)&&max>0&&finite(value)&&value>=0&&value<=max;
  return '<div class="'+(calculated?'':'u-legacy-unverified')+'"><span>'+esc(name)+'</span><b>'+
   (calculated?value.toFixed(1):'미검증')+' / '+esc(max)+'</b></div>';
 }).join('');
 view.hidden=false;
 view.innerHTML='<div class="u-detail-top"><div><small>종목별 분석 · '+esc(model.date)+'</small>'+
 '<h2>'+esc(r.name)+'</h2><p>'+esc(r.code)+' · '+esc(r.market)+'</p></div>'+
 '<button id="detailClose" type="button" aria-label="분석 닫기">✕</button></div>'+
 '<div class="u-verdict '+(risk.unsafe?'warning':'neutral')+'"><strong>'+esc(risk.description)+'</strong>'+
 '<span>참고 대응: '+esc(x.decision.label)+' (매수 신호 미인증)</span></div>'+
 '<div class="u-detail-grid"><div><small>종가</small><strong>'+won(r.price)+'</strong></div>'+
 '<div><small>구조 무효화 참고선</small><strong>'+won(r.stop_line)+'</strong></div>'+
 '<div><small>참고 손절거리</small><strong>'+(risk.distance===null?'미확인':pct(-risk.distance))+'</strong></div>'+
 '<div><small>기존 관측 점수</small><strong>'+r.score.toFixed(1)+'점</strong></div></div>'+
 '<h3>기존 V2.2 점수 산출근거 (별도 체계)</h3>'+
 '<p class="u-muted">확보된 항목의 획득점수 ÷ 확보된 배점 × 100. 자료 확보 '+
 r.coverage_pct.toFixed(0)+'% · V3 6영역 점수가 아닌 기존 관측점수입니다.</p>'+
 '<div class="u-parts u-legacy-parts">'+legacyParts+'</div>'+
 '<h3>일봉 · 스윙 관찰</h3>'+
 (x.daily.length?'<div class="u-signals">'+x.daily.map(patternPill).join('')+'</div>':
 '<p class="u-muted">확인된 선호 패턴·RSI 관측 없음 또는 당일 자료 미확보</p>')+
 '<h3>주봉 · 중기/장기 관찰</h3>'+
 (x.weekly.length?'<div class="u-signals">'+x.weekly.map(patternPill).join('')+'</div>':
 '<p class="u-muted">완성 주봉의 패턴·RSI 관측 미확인 (진행 중인 주봉 제외)</p>')+
 '<h3>V3 통합 100점 · 영역별 검증 상태</h3>'+
 '<p class="u-muted">확정 투자 종합점수: <b>미산출</b> · 6개 영역의 검증 자료가 충족될 때만 확정 가능. 기존 관측점수와 혼동 금지.</p>'+
 '<div class="u-pillars">'+part+'</div>'+
 '<p class="u-muted">기술적 분석 25점 안의 근거별 구성 (일봉/주봉 독립 평가)</p>'+
 technicalParts(r)+
 '<h3>재무 · 밸류 참고</h3>'+
 '<p class="u-muted">'+(annual?
  esc(annual.year)+'년 '+esc(annual.fs_div)+' 재무: 매출 '+eok(yearly.revenue)+
  ' / 영업이익 '+eok(yearly.operating_income)+
  ' / 영업현금흐름 '+eok(yearly.operating_cashflow):
  '동일 기준일에 검증 가능한 연간 공시 실적 미연결')+'</p>'+
 '<p class="u-muted">비공식 간이 P/B '+(book!==null?book.toFixed(2)+'배':'미산출')+
 ' · 정식 PBR 15점과 별개. 기준일·회계범위 확인 필요.</p>'+
 '<h3>퀀트 · 과거 유사조건</h3><p class="u-muted">'+esc(quant)+'</p>'+
 quantTable(x,core)+
 '<h3>손실 위험 · 가설 무효화</h3><p class="u-muted">'+esc(x.decision.reason)+
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
   '종목 · '+(query?'전체 시장 검색 · ':top100Only?'기존 점수 상위 100개 중 ':'전체 시장 중 ')+
   (sort==='score'?'기존 관측점수순':sort==='risk'?'손절 위험순':'패턴 관찰순')+
   ' · 정식 V3 종합순위 아님';
 $('stockList').innerHTML=rows.length?rows.slice(0,limit).map((x,i)=>{
  const r=x.r;
  const tags=x.signals.slice(0,3).map(patternPill).join('');
  const state=x.risk.unsafe?'위험 주의':x.risk.distance===null?'손절선 미확인':'구조적 위험 검토';
  return '<button class="u-stock '+(selected===r.code?'selected':'')+
   '" type="button" data-stock="'+esc(r.code)+'" aria-label="'+esc(r.name)+' 종목 상세 분석 열기">'+
   '<div class="u-stock-name"><span class="u-ranked"><b class="u-rank-number" title="전체 종목 기존 관측점수 순위">'+
   x.scoreRank+'위</b><strong>'+esc(r.name)+'</strong></span><small>'+
   esc(r.code)+' · '+esc(r.market)+'</small></div>'+
   '<div class="u-stock-main"><span><b>'+won(r.price)+'</b><small>종가</small></span>'+
   '<span><b>'+r.score.toFixed(1)+'</b><small>기존 관측점수 · 확보 '+r.coverage_pct.toFixed(0)+'%</small></span></div>'+
   '<div class="u-scoresix" aria-label="6영역 V3 점수: 미검증은 빨간 테두리로 표시">'+pillarState(r).map(i=>
     '<span class="u-factor-unverified"><small>'+esc(i.label)+' /'+i.max+'</small><b>'+
     (i.candidate===null?'미검증':i.candidate.toFixed(1))+
     '</b>'+(i.candidate===null?'':'<em class="u-factor-mark">미검증</em>')+'</span>').join('')+
     '<span><small>V3 합계 /100</small><b>미산출</b></span></div>'+
   '<p class="u-score-hint">빨간 테두리 = V3 미검증. 실제 계산된 연구 후보만 숫자로 표시하며, '+
   '근거가 없는 항목은 미검증으로 유지합니다. 위 기존 관측점수 '+
   r.score.toFixed(1)+'점은 V3 종합점수가 아닙니다.</p>'+
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
 $('statV3').textContent='미완성';
 $('sourceNotes').textContent='전체 '+(metrics.eligible||'미확인')+
  '종목 중 가격 분석 '+metrics.total+'종목 · 일/주봉 관찰 후보 '+metrics.discover+
  '종목 · V3 기술 연구값 '+metrics.technical+
  '종목 · 연간/간이 장부 참고 '+metrics.rollingBook+
  '종목 · 같은 날 퀀트 '+metrics.quant+'종목. 수급·재무·섹터 검증 전 종합100점 미산출.';
 draw();
}
async function load(){
 const s=$('state');s.className='u-state';s.textContent='장마감 분석 자료 확인 중…';
 const files=['data.json','quant.json','recommendations.json','official.json','v3-priority-quant.json','v3-dart-book-reference.json'];
 try{
  const responses=await Promise.all(files.map(async file=>{
   try{const r=await root.fetch(file+'?v='+Date.now(),{cache:'no-store'});
    return r.ok?await r.json():null;}catch(_){return null;}
  }));
  core=root.V3RankedCore;
  if(!core)throw Error('missing_research_core');
  model=core.prepare(...responses);
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
