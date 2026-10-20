/* V3 score-first user research: published v2.2 points + matched quant. No fabricated V3 points. */
(function(root){
'use strict';
const PERIODS=['초단기 1주','단기 2주','스윙 1개월','스윙 2개월','중기 3개월','중기 6개월','장기 1년'];
const esc=x=>String(x==null?'':x).replace(/[&<>"']/g,t=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[t]));
const finite=x=>typeof x==='number'&&Number.isFinite(x);
const money=x=>finite(x)?Math.round(x).toLocaleString('ko-KR')+'원':'미확인';
const pct=x=>finite(x)?(x>=0?'+':'')+x.toFixed(1)+'%':'미산출';
const eok=x=>finite(x)?(x/100000000).toLocaleString('ko-KR',{maximumFractionDigits:1})+'억원':'미확인';
const stockPage=code=>/^[0-9]{6}$/.test(String(code))?
  'https://finance.naver.com/item/main.naver?code='+code:null;
function observedAnnuals(official,asof){
 const byCode=new Map();
 if(!official||official.asof!==asof||!['observed','completed'].includes(official.dart?.status)
    ||!Array.isArray(official.dart.stocks))return byCode;
 for(const stock of official.dart.stocks){
  if(!stock||!/^[0-9]{6}$/.test(String(stock.code))||
    stock.status!=='observed'||!Array.isArray(stock.reports))continue;
  // Keep the latest dated annual reference AND up to three comparable
  // years of the SAME accounting scope. Do not blend consolidated CFS with
  // separate OFS financials or attach future filings to an old market day.
  const candidates=stock.reports.filter(x=>x&&Number.isInteger(x.year)&&
    x.year<=Number(asof.slice(0,4))&&x.year>=2010&&
    (x.fs_div==='CFS'||x.fs_div==='OFS')&&x.currency==='KRW'&&
    typeof x.values==='object'&&x.values!==null&&
    typeof x.receipt_id==='string'&&/^\d{14}$/.test(x.receipt_id)&&
    x.receipt_id.slice(0,8)<=asof.replaceAll('-',''))
    .sort((a,b)=>b.year-a.year||(a.fs_div==='CFS'?-1:1));
  const annual=candidates[0];
  if(annual){
    const seenYears=new Set(),history=[];
    for(const report of candidates){
      if(report.fs_div!==annual.fs_div||seenYears.has(report.year))continue;
      seenYears.add(report.year);history.push(report);
      if(history.length>=3)break;
    }
    byCode.set(stock.code,{...annual,history});
  }
 }
 return byCode;
}
function observedRollingBook(rolling,asof,eligible){
 const found=new Map();
 if(!rolling||rolling.schema!=='v3-dart-current-listed-annual-book-reference-0.1'||
   rolling.asof!==asof||!Number.isInteger(eligible)||rolling.market_listed_total!==eligible||
   rolling.official_exchange_pbr_verified!==false||
   rolling.historical_point_in_time_verified!==false||
   rolling.stock_buy_certified!==false||rolling.score_weight_changed!==false||
   rolling.raw_dart_account_rows_published!==false||
   !Array.isArray(rolling.records)||!Array.isArray(rolling.observations)||
   rolling.records.length!==rolling.attempted_reference_count||
   rolling.observations.length!==rolling.observed_annual_count||
   rolling.records.length>eligible||rolling.observations.length>rolling.records.length)
   return found;
 const allowed=new Set(),accounts=new Map();
 for(const record of rolling.records){
   if(!record||typeof record.code!=='string'||!/^\d{6}$/.test(record.code)||
      allowed.has(record.code)||!['observed','unconfirmed','deferred'].includes(record.status))
     return new Map();
   allowed.add(record.code);
   if(record.status==='observed'){
     const a=record.annual;
     if(!a||!Number.isInteger(a.year)||a.year<2018||a.year>Number(asof.slice(0,4))-1||
        !['OFS','CFS'].includes(a.fs_div)||typeof a.receipt_id!=='string'||
        !/^\d{14}$/.test(a.receipt_id)||a.receipt_id.slice(0,8)>asof.replaceAll('-',''))
       return new Map();
     accounts.set(record.code,a);
   }
 }
 const seen=new Set();
 for(const row of rolling.observations){
   if(!row||!/^\d{6}$/.test(String(row.code))||seen.has(row.code)||
      !accounts.has(row.code)||row.market_cap_reference_day!==asof||
      row.official_exchange_pbr_verified!==false||
      row.financial_statement_pit_verified!==false) return new Map();
   const a=accounts.get(row.code);
   if(a.receipt_id!==row.receipt_id||a.year!==row.year||a.fs_div!==row.fs_div)
     return new Map();
   seen.add(row.code);
   if(finite(row.pbr_simple_annual_reference)&&row.pbr_simple_annual_reference>0&&
      row.pbr_simple_annual_reference<1000)
     found.set(row.code,{
       value:row.pbr_simple_annual_reference,
       year:row.year,fs_div:row.fs_div,receipt_id:row.receipt_id
     });
   else if(row.pbr_simple_annual_reference!==null)return new Map();
 }
 return found;
}
function observedFourYearEvidence(source,asof,eligible){
 const empty=new Map();
 // Annual reports do not change on every trading day. Do not hide a valid
 // four-year reference merely because the price snapshot rolled forward.
 // Reject future or old references rather than silently recycling stale FYs.
 const sourceDate=Date.parse(String(source?.asof||'')+'T00:00:00Z');
 const targetDate=Date.parse(String(asof||'')+'T00:00:00Z');
 const sourceAge=Math.round((targetDate-sourceDate)/86400000);
 if(!source||source.schema!=='v3-four-year-dart-financial-evidence-0.1'||
   !Number.isFinite(sourceAge)||sourceAge<0||sourceAge>30||
   source.listed_total!==eligible||
   source.four_years_all_financial_pillars_certified!==false||
   source.full_v3_ready!==false||
   source.raw_account_rows_stored!==false||
   source.confirmed_six_pillar_ranks!==null||
   !Array.isArray(source.records)||source.records.length>eligible||
   source.records.length!==source.pilot_issuer_count)return empty;
 const result=new Map(),seen=new Set();
 for(const item of source.records){
  if(!item||typeof item.code!=='string'||!/^[0-9]{6}$/.test(item.code)||
     seen.has(item.code)||item.v3_full100_investable_score!==null||
     item.financial15_confirmed_points!==null||
     item.valuation15_confirmed_points!==null||
     item.four_year_financial_pit_authenticated!==false||
     item.historical_revisions_authenticated!==false||
     item.buy_certified!==false)return new Map();
  seen.add(item.code);
  if(item.same_scope_four_years!==true)continue;
  const reports=item.reports;
  if(!Array.isArray(reports)||reports.length!==4||
      !['CFS','OFS'].includes(item.accounting_scope))return new Map();
  const years=reports.map(x=>x?.year);
  if(years.some((y,i)=>!Number.isInteger(y)||y<2015||
       y>=Number(asof.slice(0,4))||(i>0&&y!==years[i-1]+1))||
     !reports.every(x=>x?.fs_div===item.accounting_scope&&x.currency==='KRW'&&
       typeof x.receipt_id==='string'&&/^[0-9]{14}$/.test(x.receipt_id)&&
       x.receipt_id.slice(0,8)<=asof.replaceAll('-','')&&
       x.receipt_id.slice(0,4)>String(x.year)))return new Map();
  const growth=item.annual_revenue_cagr3_reference_pct;
  const margin=item.three_year_operating_margin_mean_reference_pct;
  if((growth!==null&&!finite(growth))||(margin!==null&&!finite(margin))||
     item.fcf_assets_reference_pct!==null||item.net_debt_equity_reference_pct!==null||
     item.valuation_official_pbr!==null)return new Map();
  result.set(item.code,{
    years,scope:item.accounting_scope,
    cagr:growth,margin,authenticated:false,
    originalReceiptsNotPITVerified:true
  });
 }
 return result;
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
function validatedPatternResearch(r){
 const p=r?.pattern_research;
 if(!p||p.schema!=='v3-pattern-first-daily-research-0.1'||
    p.timeframe!=='daily'||p.status!=='heuristic_unverified'||
    p.asof!==r.price_date||
    p.scoring_allowed!==false||p.buy_signal!==false||
    p.official_regular_session_verified!==false||
    !Array.isArray(p.patterns)||p.patterns.length>6||
    (p.rsi14!==null&&(!finite(p.rsi14)||p.rsi14<0||p.rsi14>100))||
    typeof p.rsi30_cross_recent!=='boolean'||
    typeof p.rsi_bullish_divergence_recent!=='boolean')return null;
 const seen=new Set();
 for(const item of p.patterns){
   if(!item||!['bull_flag','double_bottom'].includes(item.type)||
      !['forming','breakout_observed'].includes(item.stage)||seen.has(item.type))
     return null;
   seen.add(item.type);
 }
 return p;
}
function watchEvidence(r){
 // Technical pattern / RSI first. Never require OBV or assume buyer identity.
 const risk=stopDistance(r);
 if(!r||!finite(r.score)||r.score<60||
    !finite(r.coverage_pct)||r.coverage_pct<55||highRisk(r)||
    risk===null||risk<3||risk>15||!finite(r.price60)||
    r.price60>35||r.price60< -35||!finite(r.price20)||r.price20>18)
   return null;
 const p=validatedPatternResearch(r);
 const prioritized=p?.patterns?.map(t=>({
   label:t.type==='bull_flag'?'Bull Flag(상승 깃발형)':'더블바텀',
   stage:t.stage
 }))||[];
 const favorites=(Array.isArray(r.patterns)?r.patterns:[])
  .filter(t=>typeof t==='string'&&/쐐기|역헤드|삼각|수렴|횡보|컵.*핸들|더블바텀|쌍바닥|깃발/.test(t))
  .slice(0,3);
 const rsi30=p?.rsi30_cross_recent===true;
 const bullishDiv=p?.rsi_bullish_divergence_recent===true;
 const phase=typeof r.phase==='string'&&/초기|전환|수렴|바닥|반등/.test(r.phase);
 const lag=r.obv_rising===true&&r.divergence===true&&
   r.price60>=-12&&r.price60<=10;
 if(!prioritized.length&&!favorites.length&&!rsi30&&!bullishDiv&&!phase)return null;
 const labels=[];
 labels.push(...prioritized.map(t=>t.label+(t.stage==='forming'?' 형성 중':' 돌파 관측')));
 labels.push(...favorites.map(t=>'패턴 근사: '+t));
 if(rsi30)labels.push('RSI 30 상향 재돌파(최근 7거래일)');
 if(bullishDiv)labels.push('RSI 상승다이버전스(확정 피벗, 최근 관측)');
 if(!labels.length&&phase)labels.push('위치: '+r.phase);
 if(lag)labels.push('OBV·가격 미반영 보조 관측');
 const strength=(prioritized.some(t=>t.label==='Bull Flag(상승 깃발형)'||t.label==='더블바텀')?3:0)+
   (favorites.length?2:0)+(rsi30||bullishDiv?1:0);
 return {obvLag:lag,pattern:prioritized.length+favorites.length>0,
  rsiSignal:rsi30||bullishDiv,phase:!!phase,
  strength,labels:labels.slice(0,5),rsi14:p?.rsi14??null};
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
function prepare(data,quant,recs,official,priorityQuant,rollingAnnual,fourYearAnnual){
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
   finite(priorityQuant.peer_cohort_requested)&&priorityQuant.peer_cohort_requested>=20&&
   priorityQuant.peer_cohort_requested<=120&&
   finite(priorityQuant.peer_cohort_valid)&&priorityQuant.peer_cohort_valid>=20&&
   priorityQuant.peer_cohort_valid<=priorityQuant.peer_cohort_requested&&
   Array.isArray(priorityQuant.stocks)&&priorityQuant.stocks.length<=20;
 const pmap=new Map((priorityMatched?priorityQuant.stocks:[]).filter(x=>
    x&&x.reference_price_date===data.date&&
    (x.status==='exploratory'||x.status==='insufficient')&&
    /^[0-9]{6}$/.test(String(x.code))&&rmap.has(x.code)&&
    finite(x.peer_cohort_requested)&&x.peer_cohort_requested>=20).map(x=>[x.code,x]));
 const financials=observedAnnuals(official,data.date);
 const books=observedRollingBook(rollingAnnual,data.date,data.eligible_count);
 const fourYear=observedFourYearEvidence(fourYearAnnual,data.date,data.eligible_count);
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
   // Validated top-level peer coverage backs a sparse per-ticker pilot.
   // Never render "undefined" as an observed market sample size.
   const peerCount=priority&&finite(priority.peer_cohort_valid)?
     priority.peer_cohort_valid:priorityMatched?priorityQuant.peer_cohort_valid:null;
   const quantSource=priority&&q===priority?
     '추천 우선 연구 '+priority.peer_cohort_requested+'종목 요청 / '+peerCount+'종목 실제 이력 확보':
     standard&&q===standard?'일반 연구 최대 120종목 표본':'연구 자료 없음';
   const qualified=!!rec&&rec.qualification==='needs_deeper_evidence'&&!highRisk(r);
   const caution=!!rec&&!qualified;
   const observedDecision=decision(r,q);
   const finalDecision=caution&&observedDecision.label!=='매수 보류'?
     {label:'매수 보류',reason:'위험 경고·연구 전용 후보 — '+observedDecision.reason,level:'wait'}:
     observedDecision;
   rows.push({r,q,rec,watch:watchEvidence(r),quantSource,financialRef:financials.get(r.code)||null,
     rollingBook:books.get(r.code)||null,
     fourYearRef:fourYear.get(r.code)||null,quant:!!q,
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
  quantCount:rows.filter(x=>x.quant).length,watchCount:rows.filter(x=>!!x.watch).length,
  patternWatchCount:rows.filter(x=>x.watch?.pattern).length,
  rsiWatchCount:rows.filter(x=>x.watch?.rsiSignal).length,
  obvLagCount:rows.filter(x=>x.watch?.obvLag===true).length,recommendationCount:selected.length,
  recommendationQuantCount:withStudy,recommendationUsableQuantCount:withUsable,
  recommendationInsufficientCount:selected.filter(x=>x.quant&&!researchReady(x)).length,
  recommendationCautionCount:selected.filter(x=>x.caution).length,
  recommendationAwaitingCount:selected.length-withStudy,
  rollingBookCount:rows.filter(x=>x.rollingBook!==null).length,
  fourYearObservedCount:rows.filter(x=>x.fourYearRef!==null).length,
  financialReferenceCount:rows.filter(x=>x.financialRef!==null).length,
  recommendationFinancialCount:selected.filter(x=>x.financialRef!==null).length};
}
function select(data,mode,market='all',search=''){
 const query=String(search).toLocaleLowerCase('ko-KR').trim();
 const results=data.rows.filter(x=>(market==='all'||market===x.r.market)&&
  (!query||(x.r.name+' '+x.r.code+' '+(x.r.sector||'')).toLocaleLowerCase('ko-KR').includes(query))&&
  (mode==='watch'?!!x.watch:mode==='pattern'?x.watch?.pattern===true:
   mode==='rsi'?x.watch?.rsiSignal===true:
   mode==='obv'?x.r.obv_rising===true&&x.r.divergence===true&&
     x.r.price60>=-12&&x.r.price60<=10&&!x.highRisk&&x.risk!==null&&
     x.risk>=3&&x.risk<=15:
   mode==='recommended'?x.researchCandidate:mode==='quant'?x.quant:
   mode==='early'?(!x.highRisk&&x.risk!==null&&x.risk>=3&&x.risk<=15&&/초기|전환|수렴|바닥/.test(String(x.r.phase))&&x.r.coverage_pct>=55):true));
 if(['watch','pattern','rsi'].includes(mode))results.sort((a,b)=>
   (b.watch?.strength??0)-(a.watch?.strength??0)||
   Number(b.watch?.rsiSignal)-Number(a.watch?.rsiSignal)||
   b.r.score-a.r.score||b.r.coverage_pct-a.r.coverage_pct||
   a.r.code.localeCompare(b.r.code));
 return results;
}
function financialHistoryTable(annual){
 const data=Array.isArray(annual?.history)?annual.history:[];
 if(data.length<2)return '';
 const rows=data.map(y=>
   '<tr><th>'+y.year+'년</th><td>'+eok(y.values?.revenue)+'</td>'+
   '<td>'+eok(y.values?.operating_income)+'</td>'+
   '<td>'+(finite(y.values?.operating_margin_pct)?
      y.values.operating_margin_pct.toFixed(1)+'%':'미확인')+'</td>'+
   '<td>'+eok(y.values?.operating_cashflow)+'</td>'+
   '<td>'+(finite(y.values?.debt_to_equity_pct)?
      y.values.debt_to_equity_pct.toFixed(1)+'%':'미확인')+'</td></tr>').join('');
 return '<p class="note">'+esc(annual.fs_div)+
   ' 동일 회계범위의 연간 공시 3개년(최대) 관측 비교. 연결/별도 혼용 없음.</p>'+
   '<div class="quant-scroller"><table class="financial-table"><thead><tr>'+
   '<th>연도</th><th>매출액</th><th>영업이익</th><th>영업이익률</th>'+
   '<th>영업현금흐름</th><th>부채비율</th></tr></thead><tbody>'+
   rows+'</tbody></table></div>';
}
function simpleAnnualOfsBookReference(row,annual){
 // This is NOT exchange-reported PBR and MUST NOT fill the v2 PBR factor.
 // OFS total equity and current market cap are different reporting dates.
 if(!annual||annual.fs_div!=='OFS'||!finite(row?.market_cap_eok)||
     row.market_cap_eok<=0||!finite(annual.values?.equity)||
     annual.values.equity<=0)return null;
 const ratio=row.market_cap_eok*1e8/annual.values.equity;
 return finite(ratio)&&ratio>0&&ratio<10000?
   Math.round(ratio*100)/100:null;
}
function sourceCompleteness(model,coverage,partial){
 // Validate source date and accounting before showing aggregate completeness.
 // Distinguish unavailable *measurements* from observed zero shares/PBR.
 if(!model||!coverage||coverage.schema!=='v22-full-market-accounting-0.1'||
    coverage.asof!==model.date||coverage.complete_batch_census!==true||
    !Number.isInteger(coverage.market_listing_size)||
    coverage.market_listing_size!==model.marketEligible)return null;
 const nums=['priced_current_session_count','price_failure_count',
  'insufficient_price_history_count','unattempted_batch_count',
  'flow_confirmed_count','pbr_observed_count'];
 if(nums.some(k=>!Number.isInteger(coverage[k])||coverage[k]<0||
       coverage[k]>coverage.market_listing_size))return null;
 const n=coverage.market_listing_size;
 if(coverage.priced_current_session_count+coverage.price_failure_count+
    coverage.insufficient_price_history_count+coverage.unattempted_batch_count!==n||
    coverage.priced_current_session_count<model.rows.length)return null;
 let partialN=null;
 if(partial&&partial.schema==='v22-current-listed-partial-daily-60to159-v0.1'&&
   partial.asof===model.date&&partial.full_v22_score_allowed===false&&
   partial.confirmed_buy_signal===false&&
   Number.isInteger(partial.listed_insufficient_history_count)&&
   partial.listed_insufficient_history_count===coverage.insufficient_price_history_count&&
   Number.isInteger(partial.partial_observed_count)&&
   partial.partial_observed_count>=0&&
   partial.partial_observed_count<=coverage.insufficient_price_history_count)
   partialN=partial.partial_observed_count;
 return {date:model.date,total:n,priced:coverage.priced_current_session_count,
  errors:coverage.price_failure_count,
  short:coverage.insufficient_price_history_count,partial:partialN,
  flow:coverage.flow_confirmed_count,pbr:coverage.pbr_observed_count};
}
function sourceCompletenessText(summary){
 if(!summary)return '전 종목 자료 확보 현황 확인 대기 — 공개 자료 기준일·집계 일치 여부 미확인';
 const n=summary.total.toLocaleString('ko-KR'),
  priced=summary.priced.toLocaleString('ko-KR');
 return '실제 전 종목 자료 확보 현황 ('+summary.date+'): 상장 대상 '+n+
  '종목 · 정상 가격 분석 '+priced+'종목 ('+
  (100*summary.priced/summary.total).toFixed(1)+'%) · 가격 오류 '+
  summary.errors+'종목 · 일봉 이력 부족 '+summary.short+'종목'+
  (summary.partial===null?' (부분 관측 미검증)':
   ' (부분 관측 '+summary.partial+'종목, 정식 점수 제외)')+
  ' · 기관/외국인 수급 확인 '+summary.flow+'종목 / '+n+
  ' · 정식 PBR 관측 '+summary.pbr+'종목 / '+n+
  '. 0건은 수급·PBR이 실제 0이라는 뜻이 아니라 자료 미확보일 수 있습니다.';
}
function quantGlance(q){
 if(!q)return '당일 퀀트 미확보';
 const items=[['스윙 1개월','1개월'],['중기 3개월','3개월'],['중기 6개월','6개월']]
  .map(([key,label])=>{const p=period(q,key);return p?
   label+' 평균 '+pct(p.mean_net_return_pct)+' / 손절 먼저 '+pct(p.stop_first_pct)+' (표본 '+p.sample_n+'건)':null;
  }).filter(Boolean);
 return items.length?items.join(' · '):'유효 표본 미충족 — 매수 근거 불충분';
}
const core={PERIODS,stopDistance,highRisk,validatedPatternResearch,watchEvidence,stockPage,observedScore,period,decision,prepare,select,observedAnnuals,observedRollingBook,observedFourYearEvidence,quantGlance,financialHistoryTable,simpleAnnualOfsBookReference,sourceCompleteness,sourceCompletenessText,esc,money,pct,eok};
if(typeof module!=='undefined'&&module.exports)module.exports=core;
root.V3RankedCore=core;
if(!root.document)return;
function init(){
 const D=root.document,$=id=>D.getElementById(id);
 let model=null,mode='watch',limit=24;
 const status=$('rankStatus'),cards=$('rankCards'),details=$('rankDetails');
 function render(){
  if(!model)return;
  const rows=select(model,mode,$('marketFilter').value,$('rankSearch').value);
  $('rankCount').textContent=(['watch','pattern','rsi','obv'].includes(mode)?'관찰용·매수 신호 아님 · ':'')+'해당 조건 '+rows.length+'종목 · 점수 산출 '+model.rows.length+
    '종목 · 최신 퀀트 연결 '+model.quantCount+'종목';
  cards.innerHTML=rows.length?rows.slice(0,limit).map((x,i)=>{
   const r=x.r;
   const firstBook=simpleAnnualOfsBookReference(r,x.financialRef);
   const bookReference=firstBook!==null?firstBook:x.rollingBook?.value??null;
   const bookBasis=firstBook!==null?
      '현재 시가총액 ÷ 최근 별도재무제표(OFS) 연간 자기자본':
      x.rollingBook?
      ('현재 시가총액 ÷ '+x.rollingBook.year+'년 '+
       (x.rollingBook.fs_div==='OFS'?'별도 자기자본':'연결 지배기업 자기자본')):'';
   return '<article class="rank-card"><div class="top"><div>'+
     '<span class="rank-number">'+(i+1)+'</span> <strong>'+esc(r.name)+'</strong>'+
     '<p class="sub">'+esc(r.code)+' · '+esc(r.market)+' · '+esc(r.phase||'위치 미확인')+'</p></div>'+
     '<div class="rank-score">'+r.score.toFixed(1)+'점<small>v2.2 관측점수<br>확보 '+r.coverage_pct.toFixed(0)+'%</small></div></div>'+
     (x.caution?'<p class="rank-caution"><b>위험 경고·연구 전용:</b> 매수 후보로 승격되지 않았습니다.</p>':'')+
     ((['watch','pattern','rsi'].includes(mode))&&x.watch?
       '<p class="rank-watchline"><b>기술적 관찰 대상 · 매수 미인증</b> · '+
       esc(x.watch.labels.join(' · '))+
       ' · 외국인·기관 수급 미확인 시 매집 단정 금지</p>':'')+
     '<div class="rank-metrics"><div><small>종가</small><b>'+money(r.price)+'</b></div>'+
     '<div><small>손절 참고선</small><b>'+money(r.stop_line)+'</b></div>'+
     '<div><small>현재 대응</small><b>'+esc(x.decision.label)+'</b></div>'+
     '<div><small>퀀트 연구</small><b>'+(x.quant?
       (['스윙 1개월','스윙 2개월','중기 3개월','중기 6개월'].some(k=>period(x.q,k))?
        '유사통계 있음':'표본 미충족'):'최신 연구 대기')+'</b></div></div>'+
     '<p class="rank-quant-line">'+esc(quantGlance(x.q))+'</p>'+
     '<p class="rank-finance-reference">'+
      (bookReference!==null?
       '<b>비공식 간이 P/B '+bookReference.toFixed(2)+'배</b> · '+esc(bookBasis)+'. 정식 PBR 점수 제외.':
       '비공식 간이 P/B 미산출 · 동일 기준일 재무/시가총액 자료 확인 필요. 정식 PBR 점수 제외.')+
     '</p>'+
     '<div class="rank-foot"><span>'+esc(x.decision.reason)+'</span>'+
     '<div class="rank-actions"><a class="rank-external" target="_blank" rel="noopener noreferrer" href="'+esc(stockPage(r.code))+'">종목 차트 ↗</a>'+ 
     '<button type="button" data-show="'+esc(r.code)+'">점수·퀀트 검증 보기</button></div></div></article>';
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
   r.coverage_pct.toFixed(0)+'%. <b>v3의 IAS20·기술25·재무15·가치15·산업15·모멘텀10 점수와 다릅니다.</b></p>'+
   '<div class="factor-grid">'+parts+'</div>'+
   '<h3>② 매수 무효화 기준과 하락 위험</h3><div class="rank-metrics">'+
   '<div><small>종가</small><b>'+money(r.price)+'</b></div>'+
   '<div><small>구조 손절 참고선</small><b>'+money(r.stop_line)+'</b></div>'+
   '<div><small>참고 손절거리</small><b>'+(risk===null?'미확인':pct(-risk))+'</b></div>'+
   '<div><small>PBR 참고값</small><b>'+(finite(r.pbr)?r.pbr.toFixed(2)+'배':'미확인')+'</b></div></div>'+
   '<p class="note">위험 경고: '+(flags.length?esc(flags.join(' · ')):'추가 위험 경고 등록 없음')+
   '. 실제 손절 체결 가격·손실은 이론값보다 나쁠 수 있습니다.</p>'+
   (x.watch?'<p class="rank-watchline"><b>차트 관찰 근거</b> · '+
     esc(x.watch.labels.join(' · '))+
     '. 기술적 휴리스틱으로 확정된 기관 매집·상승확률이 아닙니다.</p>':'')+
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
      '</b></div></div>'+
     (simpleAnnualOfsBookReference(r,x.financialRef)!==null?
       '<p class="note"><b>비공식 간이 P/B 참고: '+
        simpleAnnualOfsBookReference(r,x.financialRef).toFixed(2)+'배</b> · 현재 시가총액 ÷ '+
        x.financialRef.year+'년 별도(OFS) 기말 자기자본. 공시일·시총 시점 불일치, 우선주·주식수·정정 이력 미검증. '+
        '<b>거래소 공인 PBR이나 기존 점수의 PBR 5점을 대신하지 않습니다.</b></p>':
       '<p class="note">간이 P/B 참고 미산출 — 검증 가능한 별도 자기자본 및 현재 시총 동시 확보가 필요합니다.</p>')+
     financialHistoryTable(x.financialRef):
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
   const urls=['data.json','quant.json','recommendations.json','official.json','v3-priority-quant.json','v22-coverage.json','v22-partial-history.json','v3-dart-book-reference.json'];
   const src=await Promise.all(urls.map(async name=>{
    try{const r=await root.fetch(name+'?rank='+Date.now(),{cache:'no-store'});return r.ok?await r.json():null}
    catch(_){return null;}
   }));
   model=prepare(...src.slice(0,5),src[7]);
   $('rankSourceQuality').textContent=sourceCompletenessText(
     sourceCompleteness(model,src[5],src[6]));
   status.textContent='기준일 '+model.date+' · 패턴·RSI 우선 관찰 '+model.watchCount+'종목 (패턴 '+model.patternWatchCount+' · RSI '+model.rsiWatchCount+' · OBV 보조 '+model.obvLagCount+') · 점수 산출 '+model.rows.length+
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
   $('rankCandidateQuant').textContent='누적 OpenDART 간이 장부가 참고 '+model.rollingBookCount+
     '종목 (정식 PBR 및 매수점수 제외) · 연간 공시 참고자료 '+model.recommendationFinancialCount+
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
   $('rankSourceQuality').textContent='전 종목 품질 현황도 데이터 불일치로 확인 대기';
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
root.document.addEventListener('DOMContentLoaded',()=>{
 // The exported research core also powers the unified primary workspace.
 // Mount the legacy beta UI only when its own controls exist.
 if(root.document.getElementById('rankStatus'))init();
});
})(typeof window!=='undefined'?window:globalThis);