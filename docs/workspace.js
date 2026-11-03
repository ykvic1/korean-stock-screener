/* Unified single-surface Korean-stock research. Source-aware, no fabricated V3 totals. */
(function(root){
'use strict';
const VERSION='v3-unified-research-workspace-0.4-informational-sector-rs';
const WEIGHTS=Object.freeze({volume:20,technical:25,financial:20,valuation:20,momentum:15});
const PILLARS=Object.freeze([['volume','거래량·OBV'],['technical','기술적 분석'],['financial','재무'],['valuation','밸류에이션'],['momentum','모멘텀']]);
const FISCAL_SUBFACTORS=Object.freeze({
 financial:{revenue_cagr_3y_pct:['3년 매출성장률',4],operating_margin_3y_pct:['3년 영업이익률',4],liabilities_to_assets_pct:['부채/자산',4],operating_roa_3y_pct:['영업 ROA',4],fcf_assets_3y:['3년 FCF/자산',4]},
 valuation:{pbr:['PBR',5],earnings_yield_pct:['이익수익률',5],sales_to_market_cap:['매출/시가총액',5],fcf_yield:['FCF수익률',5]}
});
const KSIC2_NAMES=Object.freeze({
 '01':'농업','02':'임업','03':'어업','05':'석탄·원유·천연가스 광업',
 '06':'금속광업','07':'비금속광물 광업','08':'광업 지원서비스',
 '10':'식료품','11':'음료','12':'담배','13':'섬유제품','14':'의복·액세서리',
 '15':'가죽·가방·신발','16':'목재·나무제품','17':'펄프·종이',
 '18':'인쇄·기록매체','19':'석유정제품','20':'화학제품(의약품 제외)',
 '21':'의약품','22':'고무·플라스틱','23':'비금속 광물제품','24':'1차 금속',
 '25':'금속가공제품','26':'전자부품·컴퓨터·통신장비','27':'의료·정밀·광학기기',
 '28':'전기장비','29':'기타 기계·장비','30':'자동차·트레일러','31':'기타 운송장비',
 '32':'가구','33':'기타 제품','34':'산업용 기계·장비 수리','35':'전기·가스',
 '36':'수도','37':'하수·폐수 처리','38':'폐기물 처리·원료재생',
 '39':'환경 정화·복원','41':'종합 건설','42':'전문직별 공사',
 '45':'자동차·부품 판매','46':'도매·상품중개','47':'소매(자동차 제외)',
 '49':'육상 운송','50':'수상 운송','51':'항공 운송','52':'창고·운송지원',
 '55':'숙박','56':'음식점·주점','58':'출판','59':'영상·음향 콘텐츠',
 '60':'방송','61':'통신','62':'소프트웨어·시스템통합','63':'정보서비스',
 '64':'금융','65':'보험·연금','66':'금융·보험 관련 서비스',
 '68':'부동산','69':'임대(부동산 제외)','70':'연구개발','71':'전문서비스',
 '72':'엔지니어링·과학기술 서비스','73':'기타 전문·기술 서비스',
 '74':'사업시설 관리·조경','75':'사업지원 서비스','76':'임대업',
 '84':'공공행정','85':'교육','86':'보건','87':'사회복지',
 '90':'창작·예술·여가','91':'스포츠·오락','94':'협회·단체',
 '95':'수리','96':'기타 개인서비스'
});
const VOLUME_COMPONENTS=Object.freeze([
 ['최근20일/이전40일 거래량',6,'sustained_volume'],
 ['최근20일 20·60일선 골든크로스 · 교차점/240일선 ±12%',6,'ma20_ma60_cross_near_ma240'],
 ['OBV 상승 대비 주가 미반영',4,'obv_price_not_reflected'],
 ['거래량 급증 후 매물 소화',4,'post_spike_absorption']
]);
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
    z.market!==r.market||z.momentum_maximum!==WEIGHTS.momentum||
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
// No separate OBV-rise bonus. The MA20/60 cross 6pt requires a dated
// intersection-vs-MA240 receipt; missing cross history is not 0 points.
function observedVolumeEvidence(r){
 const v=r?.v3_volume_observed;
 if(!v||v.asof!==r.price_date||v.market!==r.market||
   !validDate(v.asof)||![10,16].includes(v.observed_available_max)||
   v.unobserved_max!==20-v.observed_available_max||
   v.source_history_adjusted_verified!==false||
   v.source_exchange_regular_only_verified!==false||
   v.foreign_institutional_net_buy_inferred!==false||
   v.investable_score_certified!==false||
   v.trading_signal_authorized!==false||
   !Array.isArray(v.risk_flags)||v.risk_flags.some(x=>typeof x!=='string')||
   !v.subpoints||typeof v.subpoints!=='object')return null;
 const z=v.subpoints;
 const cross=z.ma20_ma60_cross_near_ma240;
 if(z.post_spike_absorption!==null ||
   !finite(z.sustained_volume)||!finite(z.obv_price_not_reflected)||
   z.sustained_volume<0||z.sustained_volume>6||
   z.obv_price_not_reflected<0||z.obv_price_not_reflected>4||
   (v.observed_available_max===10?cross!==null:!(cross===0||cross===6))||
   !finite(v.observed_points)||
   Math.abs(v.observed_points-(z.sustained_volume+
      z.obv_price_not_reflected+(cross??0)))>0.000001)return null;
 return {earned:v.observed_points,available:v.observed_available_max,fullyObserved:false};
}
// Saved 60-session OBV rise with price lag [-12,+10]%; research only.
// Not independently adjusted divergence, institutional flow, or a buy signal.
function obvLagCandidate(r){
 const evidence=observedVolumeEvidence(r);
 const z=r?.v3_volume_observed?.subpoints;
 return evidence!==null&&z?.obv_price_not_reflected===4;
}
function applyObservedVolume(model,source){
 if(!model||!Array.isArray(model.rows)||!validDate(model.date)||
   !Number.isInteger(model.marketEligible)||model.marketEligible<1||
   !source||source.schema!=='v3-current-market-volume-ma-cross-provisional-research-0.2'||
   source.asof!==model.date||source.listed_total!==model.marketEligible||
   source.points_budget!==20||
   source.maximum_available_point_budget_per_observed_issuer!==16||
   source.base_available_point_budget_per_observed_issuer!==10||
   source.ma_cross_unavailable_is_not_zero!==true||
   source.not_observed_absorption_max!==4||
   source.ias_investor_flow_component_used!==false||
   source.technical_one_day_volume_not_double_counted!==true||
   source.source_raw_ohlcv_published!==false||
   source.corporate_actions_adjusted!==false||
   source.official_foreign_institutional_flow_verified!==false||
   source.historical_PIT_certified!==false||
   source.certified_100_point_investment_scores!==0||
   source.research_only!==true||
   !source.records||typeof source.records!=='object'||Array.isArray(source.records)||
   !Number.isInteger(source.price_source_issuers)||
   source.price_source_issuers!==model.rows.length||
   !Number.isInteger(source.observed_volume_candidate_issuers)||
   !Number.isInteger(source.insufficient_or_unpriced_issuers)||
   source.insufficient_or_unpriced_issuers+
    source.observed_volume_candidate_issuers!==source.listed_total||
   !/^[a-f0-9]{64}$/.test(source.source_market_sha256||'')||
   !/^[a-f0-9]{64}$/.test(source.source_catalog_sha256||''))
   return 0;
 const list=new Map(model.rows.map(x=>[x.r.code,x.r]));
 if(list.size!==model.rows.length)return 0;
 const accepted=[];
 for(const [code,rec] of Object.entries(source.records)){
  const r=list.get(code);
  if(!TODAY_CODE.test(code)||!r||!rec||typeof rec!=='object'||
     r.price_date!==model.date||
     observedVolumeEvidence({...r,v3_volume_observed:rec})===null)return 0;
  accepted.push([r,rec]);
 }
 if(accepted.length!==source.observed_volume_candidate_issuers)return 0;
 for(const [r,rec] of accepted)r.v3_volume_observed=rec;
 return accepted.length;
}
// Independently stored same-day market-price replay, at most technical25
// + momentum8 points. This is not a new KRX/DART fetch or a full 100 rank.
function applyCurrentPriceReplay(model,source){
 if(!model||!Array.isArray(model.rows)||!validDate(model.date)||
   !Number.isInteger(model.marketEligible)||
   !source||source.schema!=='v3-all-listed-current-market-price-only-technical-momentum-replay-0.1'||
   source.asof!==model.date||source.listed_total!==model.marketEligible||
   source.priced_current_session!==model.rows.length||
   !Number.isInteger(source.technical_research_issuer_count)||
   !Number.isInteger(source.momentum_research_issuer_count)||
   !Number.isInteger(source.at_least_one_price_factor_issuer_count)||
   source.separate_source_provider_calls!==0||
   source.official_ias_or_buyer_identity_inferred!==false||
   source.corporate_action_adjusted_verified!==false||
   source.historical_PIT_certified!==false||
   source.sector_score_authentic!==false||
   source.certified_100point_investment_issuers!==0||
   !/^[a-f0-9]{64}$/.test(source.source_market_sha256||'')||
   !/^[a-f0-9]{64}$/.test(source.source_catalog_sha256||'')||
   !source.rows||typeof source.rows!=='object'||Array.isArray(source.rows))
   return 0;
 const stocks=new Map(model.rows.map(x=>[x.r.code,x.r]));
 if(stocks.size!==model.rows.length)return 0;
 let tech=0,momentum=0,accepted=[];
 for(const [code,entry] of Object.entries(source.rows)){
  const r=stocks.get(code);
  if(!TODAY_CODE.test(code)||!r||!entry||typeof entry!=='object'||
    entry.code!==code||entry.market!==r.market||
    entry.asof!==model.date||r.price_date!==model.date||
    entry.official_investment_score_certified!==false||
    entry.price_source_corporate_action_verified!==false)
    return 0;
  const t=entry.technical_research_reference;
  const m=entry.momentum_research_reference;
  if(t===null&&m===null)return 0;
  if(t!==null){
   const ev=technicalEvidence({...r,v3_pattern_integrated:t});
   if(!ev||ev.available>25)return 0;
   tech++;
  }
  if(m!==null){
   const ev=momentumEvidence({...r,v3_momentum_research:m});
   if(!ev||ev.available>8)return 0;
   momentum++;
  }
  accepted.push([r,t,m]);
 }
 if(tech!==source.technical_research_issuer_count||
    momentum!==source.momentum_research_issuer_count||
    accepted.length!==source.at_least_one_price_factor_issuer_count)
   return 0;
 let added=0;
 for(const [r,t,m] of accepted){
  if(t&&(!r.v3_pattern_integrated||
     !technicalEvidence(r))){
   r.v3_pattern_integrated=t;
   added++;
  }
  if(m&&!momentumEvidence(r)){
   r.v3_momentum_research=m;
   added++;
  }
 }
 return added;
}
// Same-day CSV is replayed from independently checked DART and market
// sources. Display its observed financial/value subweights exactly once;
// never infer full scores or combine different market dates.
const CURRENT_COLUMNS=Object.freeze([
 '종목코드','종목명','시장','가격자료기준일','거래량_OBV_잠정점수',
 '거래량_OBV_산출배점','기술_잠정점수','기술_산출배점',
 '재무_잠정점수','밸류에이션_잠정점수',
 '모멘텀_잠정점수','모멘텀_산출배점',
 '관측잠정합계_100점중','산출가능배점_100점중',
 '미확보배점_100점중','정식투자점수','투자등급',
 '재무_산출배점','가치_산출배점',
 'FCF자산3년_잠정점수','FCF수익률_잠정점수'
]);
// Accept the old CSV only during an atomic hourly publisher transition.
// The superseded industry column MUST be blank; it never creates points.
const LEGACY_CURRENT_COLUMNS=Object.freeze([
 ...CURRENT_COLUMNS.slice(0,10),'산업_잠정점수',...CURRENT_COLUMNS.slice(10)
]);
function parseObservedCsv(input){
 if(typeof input!=='string'||input.length>2000000||input.length<50)return null;
 const raw=input.charCodeAt(0)===0xfeff?input.slice(1):input;
 const records=[];
 let parts=[],part='',quoted=false,closed=false;
 for(let i=0;i<raw.length;i++){
  const c=raw[i];
  if(quoted){
   if(c==='"'){
    if(raw[i+1]==='"'){part+='"';i++;}
    else{quoted=false;closed=true;}
   }else part+=c;
  }else if(c===','||c==='\n'){
   parts.push(part);part='';closed=false;
   if(c==='\n'){records.push(parts);parts=[];if(records.length>6001)return null;}
  }else if(c==='"'&&!part&&!closed){quoted=true;}
  else if(c==='\r'&&raw[i+1]==='\n')continue;
  else if(closed||c==='"'||c==='\0')return null;
  else part+=c;
 }
 if(quoted)return null;
 if(parts.length||part){parts.push(part);records.push(parts);}
 return records;
}
function applyCurrentObservedFinance(model,summary,csv){
 if(!model||!Array.isArray(model.rows)||!validDate(model.date)||
    !Number.isInteger(model.marketEligible)||model.marketEligible<1||
    !summary||summary.schema!=='v3-current-day-all-listed-observed-plus-dart-fcf-0.3'||
    summary.asof!==model.date||summary.listed_total!==model.marketEligible||
    summary.certified_100point_count!==0||
    summary.missing_weight_is_not_zero!==true||
    summary.not_an_investment_ranking!==true||
    summary.highest_currently_observable_weight>100||
    !summary.area_budgets||
    Object.keys(summary.area_budgets).length!==PILLARS.length||
    PILLARS.some(([key])=>summary.area_budgets[key]!==WEIGHTS[key])||
    !summary.observed_issuer_counts||
    (summary.observed_issuer_counts.industry!==undefined&&
     summary.observed_issuer_counts.industry!==0))return 0;
 const rows=parseObservedCsv(csv);
 if(!rows||rows.length!==model.marketEligible+1)return 0;
 const isNew=rows[0].length===CURRENT_COLUMNS.length&&
   rows[0].every((name,i)=>name===CURRENT_COLUMNS[i]);
 const isLegacy=rows[0].length===LEGACY_CURRENT_COLUMNS.length&&
   rows[0].every((name,i)=>name===LEGACY_CURRENT_COLUMNS[i]);
 if(!isNew&&!isLegacy)return 0;
 const activeColumns=isNew?CURRENT_COLUMNS:LEGACY_CURRENT_COLUMNS;
 const number=(value,max)=>{
  if(value==='')return null;
  if(typeof value!=='string'||!value.trim()||!Number.isFinite(Number(value)))return NaN;
  const v=Number(value);
  return v>=0&&v<=max?v:NaN;
 };
 const columns=Object.fromEntries(activeColumns.map((name,i)=>[name,i]));
 const idx=name=>columns[name];
 const stockMap=new Map();
 for(const x of model.rows){
  if(!x?.r||!TODAY_CODE.test(x.r.code)||stockMap.has(x.r.code))return 0;
  stockMap.set(x.r.code,x.r);
 }
 const proposed=[],seen=new Set();
 let financialCount=0,valuationCount=0;
 for(const record of rows.slice(1)){
  if(record.length!==activeColumns.length)return 0;
  const field=name=>record[idx(name)];
  const code=field('종목코드'),market=field('시장');
  if(!TODAY_CODE.test(code)||seen.has(code)||
     !['KOSPI','KOSDAQ'].includes(market)||
     field('가격자료기준일')!==model.date||
     field('정식투자점수')!==''||
     (isLegacy&&field('산업_잠정점수')!=='')||
     field('투자등급')!=='미검증 연구용')return 0;
  seen.add(code);
  const financial=number(field('재무_잠정점수'),WEIGHTS.financial);
  const valuation=number(field('밸류에이션_잠정점수'),WEIGHTS.valuation);
  const financeAvail=field('재무_산출배점')===''?0:
    number(field('재무_산출배점'),WEIGHTS.financial);
  const valueAvail=field('가치_산출배점')===''?0:
    number(field('가치_산출배점'),WEIGHTS.valuation);
  const fcfFinance=number(field('FCF자산3년_잠정점수'),4);
  const fcfValue=number(field('FCF수익률_잠정점수'),5);
  const components=[
   [financial,financeAvail,WEIGHTS.financial],
   [valuation,valueAvail,WEIGHTS.valuation],
   [number(field('거래량_OBV_잠정점수'),20),
    field('거래량_OBV_산출배점')===''?0:number(field('거래량_OBV_산출배점'),20),20],
   [number(field('기술_잠정점수'),25),
    field('기술_산출배점')===''?0:number(field('기술_산출배점'),25),25],
   [number(field('모멘텀_잠정점수'),WEIGHTS.momentum),
    field('모멘텀_산출배점')===''?0:
      number(field('모멘텀_산출배점'),WEIGHTS.momentum),WEIGHTS.momentum]
  ];
  if(components.some(([value,avail,max])=>
      !Number.isInteger(avail)||avail<0||avail>max||
      (avail===0)!==(value===null)||
      (value!==null&&(!finite(value)||value>avail)))||
     (fcfFinance!==null&&(!finite(fcfFinance)||financeAvail<4))||
     (fcfValue!==null&&(!finite(fcfValue)||valueAvail<5)))return 0;
  const available=components.reduce((sum,x)=>sum+x[1],0);
  const subtotal=components.reduce((sum,x)=>sum+(x[0]??0),0);
  const totalAvailable=field('산출가능배점_100점중')===''?0:
    number(field('산출가능배점_100점중'),100);
  const unavailable=number(field('미확보배점_100점중'),100);
  const reported=number(field('관측잠정합계_100점중'),100);
  if(totalAvailable!==available||unavailable!==100-available||
     (available===0)!==(reported===null)||
     (reported!==null&&Math.abs(reported-subtotal)>0.002))return 0;
  if(financeAvail>0)financialCount++;
  if(valueAvail>0)valuationCount++;
  const matched=stockMap.get(code);
  if(matched){
   if(matched.market!==market||matched.price_date!==model.date)return 0;
   proposed.push([matched,{
    asof:model.date,
    financial:{points:financial,available:financeAvail},
    valuation:{points:valuation,available:valueAvail}
   }]);
  }
 }
 if(seen.size!==model.marketEligible||
    proposed.length!==stockMap.size||
    summary.observed_issuer_counts.financial!==financialCount||
    summary.observed_issuer_counts.valuation!==valuationCount)return 0;
 for(const [stock,value] of proposed)stock.v3_same_day_observed_finance=value;
 return proposed.filter(([,value])=>value.financial.available>0).length;
}
// Per-issuer FCF status uses only the date/scope-gated public preview.
// Missing preview rows do not prove which cashflow account is absent.
function fcfEvidenceStatus(r){
 const fcf=r?.v3_dart_fcf_research;
 if(!fcf)return {state:'not_linked',asof:r?.price_date??null};
 if(fcf.asof!==r?.price_date||fcf.market!==r?.market||
    fcf.strict_receipt_scope_matched!==true||
    fcf.scoring_certified!==false||fcf.historical_PIT_certified!==false)
  return {state:'source_mismatch',asof:r?.price_date??null};
 const metric=(raw,points,available,max,peers)=>{
  if(!finite(raw))return {state:'not_observed',raw:null,points:null,peers:null,max};
  if(available===max&&finite(points)&&Number.isInteger(peers)&&peers>=30)
   return {state:'provisional',raw,points,peers,max};
  if(available===0&&Number.isInteger(peers)&&peers<30)
   return {state:'insufficient_peers',raw,points:null,peers,max};
  return {state:'raw_only',raw,points:null,
   peers:Number.isInteger(peers)?peers:null,max};
 };
 return {state:'matched',asof:fcf.asof,
  threeYear:metric(fcf.raw_observed_financial_fcf_assets_3y_pct,
   fcf.financial_fcf_assets_candidate_points,
   fcf.financial_fcf_assets_available_max,4,fcf.financial_fcf_peer_count),
  latest:metric(fcf.raw_observed_valuation_fcf_yield_pct,
   fcf.valuation_fcf_yield_candidate_points,
   fcf.valuation_fcf_yield_available_max,5,fcf.valuation_fcf_peer_count)};
}
const FCF_ISSUER_LABELS=Object.freeze({
 N:'해당 연도 현금흐름 원천 미수집 · 0원 아님',
 X:'CFO와 CAPEX 1개 이상 유효값 미확보',
 C:'CFO 표준계정 유효값 미확보',
 B:'유형·무형 CAPEX 유효값 모두 미확보',
 P:'유형자산 취득 CAPEX 유효값 미확보',
 I:'무형자산 취득 CAPEX 유효값 미확보',
 U:'FCF 계정 관측 · 연간공시 접수번호 일치 미확인',
 V:'FCF 계정 관측 · 연간공시 접수번호 일치'
});
// Source-only compact issuer codes attach only after the exact cashflow
// snapshot hash, same-day listed universe and all source partitions match.
function applyIssuerCashflowEvidence(model,report,fcfPreview){
 const codes=model?.rows,entries=report?.records;
 const validHash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
 if(!Array.isArray(codes)||!codes.length||codes.length>model.marketEligible||
    !report||report.schema!=='v3-listed-issuer-annual-fcf-account-availability-0.1'||
    report.asof!==model.date||report.listed_total!==model.marketEligible||
    !Array.isArray(report.fiscal_years)||
    report.fiscal_years.join(',')!=='2023,2024,2025'||
    !validHash(report.source_cashflow_sha256)||
    !fcfPreview||fcfPreview.asof!==report.asof||
    report.source_cashflow_sha256!==fcfPreview.cashflow_snapshot_sha256||
    report.source_receipt_scope_checked!==true||
    report.unobserved_not_zero!==true||
    report.raw_amounts_or_receipt_ids_published!==false||
    report.historical_pit_verified!==false||
    report.provider_api_calls!==0||report.trading_score_promoted!==false||
    !entries||typeof entries!=='object'||Array.isArray(entries)||
    !report.status_counts||typeof report.status_counts!=='object'||
    Array.isArray(report.status_counts))return 0;
 const universe=new Map(codes.map(x=>[x.r?.code,x.r]));
 if(universe.size!==codes.length||codes.some(x=>
   x.r?.price_date!==model.date||!['KOSPI','KOSDAQ'].includes(x.r?.market)))return 0;
 const actual=Object.fromEntries(['X','C','B','P','I','U','V'].map(x=>[x,0]));
 const rows=Object.entries(entries);
 if(!Number.isInteger(report.issuers_with_saved_cashflow_years)||
    rows.length!==report.issuers_with_saved_cashflow_years||
    !Number.isInteger(report.issuers_without_saved_cashflow_years)||
    report.issuers_without_saved_cashflow_years!==model.marketEligible-rows.length||
    !Number.isInteger(report.attempted_company_years)||
    !Number.isInteger(report.unfetched_company_year_slots))return 0;
 let unfetched=3*(model.marketEligible-rows.length),attempted=0;
 for(const [code,values] of rows){
  if(!/^[0-9]{6}$/.test(code)||
     !Array.isArray(values)||values.length!==4||
     !['CFS','OFS'].includes(values[0]))return 0;
  let saved=0;
  for(const state of values.slice(1)){
   if(state==='N')unfetched++;
   else if(Object.hasOwn(actual,state)){actual[state]++;attempted++;saved++;}
   else return 0;
  }
  if(saved===0)return 0;
 }
 if(attempted!==report.attempted_company_years||
    unfetched!==report.unfetched_company_year_slots||
    unfetched+attempted!==3*model.marketEligible||
    Object.keys(report.status_counts).length!==7||
    Object.keys(actual).some(k=>report.status_counts[k]!==actual[k]))return 0;
 // Mutate only after every listed issuer/year partition passes validation.
 for(const x of codes)x.r.v3_dart_fcf_issuer_gap=
   entries[x.r.code]||['','N','N','N'];
 return rows.length;
}
function issuerCashflowGapMarkup(r){
 const e=r?.v3_dart_fcf_issuer_gap;
 if(!Array.isArray(e)||e.length!==4||
    !['','CFS','OFS'].includes(e[0])||
    e.slice(1).some(v=>!Object.hasOwn(FCF_ISSUER_LABELS,v)))return '';
 const scope=e[0]||'저장된 회계범위 없음';
 return '<div class="u-fcf-issuer-years"><p class="u-source"><b>연도별 공식 현금흐름 계정 확보 상태</b> · '+
  esc(scope)+' 기준 · 접수·계정 분류만 표시</p>'+
  '<ul class="u-fcf-stock-list">'+e.slice(1).map((code,i)=>
   '<li><b>'+(2023+i)+'년</b><span>'+FCF_ISSUER_LABELS[code]+
   '</span></li>').join('')+'</ul>'+
  '<p class="u-source">미확보는 표준 IFRS 계정이 관측되지 않았거나 유효금액 검증이 보류됐다는 뜻으로, '+
  '기업이 실제로 공시하지 않았다고 단정할 수 없습니다. 모든 연도가 관측돼도 자산·시가총액 등 분모나 공시 대조 조건으로 FCF 지표는 미산출될 수 있습니다.</p></div>';
}
function fcfEvidenceMarkup(r){
 const status=fcfEvidenceStatus(r);
 const detail=metric=>{
  if(metric.state==='not_observed')return '원자료 미산출 · 0점 아님';
  if(metric.state==='insufficient_peers')
   return '원자료 '+pct(metric.raw)+' 관측 · 동시장 비교 '+metric.peers+
    '개 (30개 미달)로 잠정 배점 보류';
  if(metric.state==='provisional')
   return '원자료 '+pct(metric.raw)+' 관측 · FCF 잠정 '+metric.points.toFixed(1)+
    '/'+metric.max+'점 (동시장 표본 '+metric.peers+'개)';
  return '원자료 '+pct(metric.raw)+' 관측 · 잠정 배점은 아직 보류';
 };
 const aligned=status.state==='matched';
 const summary=aligned?
  '3년 '+(status.threeYear.state==='not_observed'?'미산출':'관측')+
  ' / 최신 '+(status.latest.state==='not_observed'?'미산출':'관측'):
  '원천 미연결';
 let body;
 if(aligned){
  body='<p class="u-source">공식 DART 접수번호·동일 회계범위 대조 자료 · 기준일 '+
   esc(status.asof)+' (정정 이력 및 과거 효력시점 인증 전)</p>'+
   '<ul class="u-fcf-stock-list"><li><b>3개년 FCF/자산</b><span>'+
   detail(status.threeYear)+'</span></li><li><b>최근 연도 FCF수익률</b><span>'+
   detail(status.latest)+'</span></li></ul>';
 }else{
  body='<p class="u-source">'+(status.state==='source_mismatch'?
   '기준일·시장·공시 대조 상태가 일치하지 않아 기존 FCF 자료를 보류했습니다.':
   '이 종목은 현재 동일 기준일 FCF 연구 표본에 연결되지 않았습니다.')+
   ' 이는 FCF가 0원이라는 뜻이 아닙니다.</p>';
 }
 const yearEvidence=issuerCashflowGapMarkup(r);
 return '<details class="u-fcf-stock-status"><summary>FCF 확보 상태 · '+
  summary+' (자세히)</summary>'+body+yearEvidence+
  (yearEvidence?'<p class="u-source">위 연도별 구분은 이미 수집된 공식 현금흐름 표준계정의 확보 여부입니다. '+
  '원공시 정정이력·역사적 효력일 검증이나 확정 점수를 뜻하지 않습니다.</p>':
  '<p class="u-source">원자료 미산출의 정확한 계정 원인(CFO·유형/무형 CAPEX·연도별 회계범위)은 '+
  '개별 공시 원장을 추가 대조해야 구분할 수 있습니다.</p>')+
  '<p class="u-source">미확보는 0점이 아니며, 관측된 잠정 배점도 확정 투자점수가 아닙니다.</p></details>';
}
function applyIssuerFactorLedger(model,ledger){
 if(!model||!Array.isArray(model.rows)||!ledger||
    ledger.schema!=='v3-issuer-last-observed-factors-0.1'||
    !validDate(ledger.market_asof)||ledger.market_asof>model.date||
    !Number.isInteger(ledger.listed_total)||ledger.listed_total<1||
    ledger.listed_total>10000||
    ledger.active_issuer_count!==ledger.listed_total||
    ledger.investment_score_certified!==false||
    !([true,false].includes(ledger.mixed_date_sum_permitted))||
    ledger.missing_is_not_zero!==true||
    !ledger.rows||typeof ledger.rows!=='object'||Array.isArray(ledger.rows)||
    Object.keys(ledger.rows).length>15000)return 0;
 let linked=0;
 for(const {r} of model.rows){
  const saved=ledger.rows[r.code];
  if(!saved||saved.code!==r.code||saved.market!==r.market||
     saved.name!==r.name||saved.active!==true||
     !saved.factors||typeof saved.factors!=='object')continue;
  const factors={};let valid=true;
  for(const [key,e] of Object.entries(saved.factors)){
   if(!Object.prototype.hasOwnProperty.call(WEIGHTS,key)||
      !e||!validDate(e.asof)||e.asof>model.date||
      !Number.isInteger(e.available)||e.available<=0||e.available>WEIGHTS[key]||
      !finite(e.points)||e.points<0||e.points>e.available||
      !['current_close_replay','dart_bulk_snapshot'].includes(e.source)){
    valid=false;break;
   }
   factors[key]={asof:e.asof,available:e.available,points:e.points,source:e.source};
  }
  if(!valid)continue;
  const components={};
  if(saved.components&&typeof saved.components==='object'&&!Array.isArray(saved.components)){
   for(const [group,items] of Object.entries(saved.components)){
    if(!Object.prototype.hasOwnProperty.call(FISCAL_SUBFACTORS,group)||
       !items||typeof items!=='object'||Array.isArray(items))continue;
    for(const [name,e] of Object.entries(items)){
     const spec=FISCAL_SUBFACTORS[group][name];
     const source=name==='fcf_assets_3y'||name==='fcf_yield'? 'dart_fcf_subfactor':'dart_fiscal_subfactor';
     if(!spec||!e||!validDate(e.asof)||e.asof>model.date||
        e.available!==spec[1]||!finite(e.points)||e.points<0||e.points>e.available||
        e.source!==source)continue;
     (components[group]??={})[name]={asof:e.asof,available:e.available,
      points:e.points,source:e.source};
    }
   }
  }
  const componentHistory={};
  if(saved.component_history&&typeof saved.component_history==='object'&&
     !Array.isArray(saved.component_history)){
   for(const [group,items] of Object.entries(saved.component_history)){
    if(!Object.prototype.hasOwnProperty.call(components,group)||
       !items||typeof items!=='object'||Array.isArray(items))continue;
    for(const [name,previous] of Object.entries(items)){
     const current=components[group][name],spec=FISCAL_SUBFACTORS[group][name];
     if(!current||!spec||!Array.isArray(previous)||previous.length>3)continue;
     let last='0000-00-00',valid=true;
     const observations=[];
     for(const p of previous){
      if(!p||!validDate(p.asof)||p.asof<last||p.asof>current.asof||
         !finite(p.points)||p.points<0||p.points>spec[1]){valid=false;break;}
      last=p.asof;
      observations.push({asof:p.asof,points:p.points});
     }
     if(valid&&observations.length)(componentHistory[group]??={})[name]=observations;
    }
   }
  }
  r.v3_component_history=componentHistory;
  r.v3_last_known_components=components;
  r.v3_last_known_factors=factors;
  linked++;
 }
 return linked;
}
function pillarState(r){
 const tech=technicalEvidence(r);
 const momentum=momentumEvidence(r);
 const bulk=r?.v3_dart_bulk_provisional;
 const bulkReady=bulk&&bulk.scoring_certified===false&&
  bulk.confirmed_v3_score===null&&bulk.asof===r.price_date&&
  bulk.market===r.market&&
  Number.isInteger(bulk.financial_available_max)&&
  [0,4,8,12,16].includes(bulk.financial_available_max)&&
  Number.isInteger(bulk.valuation_available_max)&&
  [0,5,10,15].includes(bulk.valuation_available_max);
 const fcf=r?.v3_dart_fcf_research;
 const fcfReady=fcf&&fcf.asof===r.price_date&&fcf.market===r.market&&
   fcf.scoring_certified===false&&fcf.historical_PIT_certified===false&&
   fcf.strict_receipt_scope_matched===true;
 // Do not reclassify archived six-pillar candidate values as five-factor
 // observations. Only dated, source-replayed price/volume/DART data count.
 return PILLARS.map(([key,label])=>{
  const maximum=WEIGHTS[key];
  let candidate=null,available=0;
  if(key==='volume'){
   const v=observedVolumeEvidence(r);
   if(v){candidate=v.earned;available=v.available;}
  }
  else if(key==='technical'&&tech){candidate=tech.earned;available=tech.available;}
  else if(key==='momentum'&&momentum){
   candidate=momentum.earned;available=momentum.available;
  }
  else if(key==='financial'||key==='valuation'){
   const sameDay=r?.v3_same_day_observed_finance;
   const matched=sameDay?.asof===r.price_date?sameDay[key]:null;
   if(matched&&Number.isInteger(matched.available)&&
      matched.available>=0&&matched.available<=maximum&&
      ((matched.points===null&&matched.available===0)||
       (finite(matched.points)&&matched.points>=0&&
        matched.points<=matched.available))){
    candidate=matched.points;available=matched.available;
   } else if(bulkReady){
    const points=key==='financial'?bulk.financial_candidate_points:
     bulk.valuation_candidate_points;
    const max=key==='financial'?bulk.financial_available_max:
     bulk.valuation_available_max;
    if(max>0&&finite(points)&&points>=0&&points<=max){
     candidate=points;available=max;
    }
   }
   // Exact IFRS cashflows (FCF/assets4 or FCF yield5) are independent
   // research subfactors, not another full financial/valuation area.
   // Missing cashflow never means zero; reject already-complete area.
   if(!matched&&fcfReady&&available<maximum){
    const points=key==='financial'?fcf.financial_fcf_assets_candidate_points:
     fcf.valuation_fcf_yield_candidate_points;
    const extra=key==='financial'?fcf.financial_fcf_assets_available_max:
     fcf.valuation_fcf_yield_available_max;
    const allowed=key==='financial'?4:5;
    if(extra===allowed&&finite(points)&&points>=0&&points<=extra&&
       available+extra<=maximum){
     candidate=(candidate??0)+points;
     candidate=Math.round(candidate*1000000)/1000000;
     available+=extra;
    }
   }
  }
  const lastObserved=r?.v3_last_known_factors?.[key]??null;
  const retained=lastObserved&&validDate(lastObserved.asof)&&
   lastObserved.asof<=r.price_date&&
   Number.isInteger(lastObserved.available)&&
   lastObserved.available>0&&lastObserved.available<=maximum&&
   finite(lastObserved.points)&&lastObserved.points>=0&&
   lastObserved.points<=lastObserved.available;
  // Preserve the larger observed factor budget when a refreshed source is
  // missing or temporarily supplies a narrower partial record.
  const carried=!!retained&&lastObserved.available>available;
  if(carried){candidate=lastObserved.points;available=lastObserved.available;}
  const asof=carried?lastObserved.asof:available>0?r.price_date:null;
  return {key,label,max:maximum,candidate,available,lastObserved,asof,carried,
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
  complete:available===100,provisional:true,pillars,
  carriedPillars:pillars.filter(p=>p.carried).length,
  stalePillars:pillars.filter(p=>p.asof&&p.asof!==r.price_date).length,
  oldestAsOf:pillars.reduce((d,p)=>p.asof&&(!d||p.asof<d)?p.asof:d,null)};
}
// Personal research-only normalized scale. Missing factors are NOT zero points.
// The 75/100 and all-five-pillar floor reduces small-denominator inflation;
// it does not certify data quality, forecasts, rankings or buy decisions.
function personalHundredFromComposite(c){
 if(!c||c.earned===null||c.available<75||c.observedPillars!==5||
    !Number.isFinite(c.earned)||c.earned<0||c.earned>c.available)return null;
 return Math.round(c.earned/c.available*1000)/10;
}
function personalHundred(r){
 return personalHundredFromComposite(researchComposite(r));
}

// Resolve only an exact six-digit ticker or an unambiguous exact Korean name.
// Partial-name guesses could silently compare the wrong issuer.
// Sketch-inspired visual scorecard: earned, observable headroom and genuinely
// unobserved budgets must remain distinguishable (never impute zero for missing).
// Every expansion preserves the underlying source-gated provisional
// candidate. Do not infer subfactor scores from the remaining weight.
// A validated last observation remains the displayed score and participates
// in research ranking when a narrower/newer observation is unavailable.
function lastObservedFactorChip(p){
 if(p.available===0||!p.asof||!validDate(p.asof))return '';
 return '<small class="u-factor-previous">'+esc(p.asof)+' 기준'+
  (p.carried?' · 이전 관측 유지':'')+'</small>';
}
// Explain source coverage without treating missing budgets as lost score points.
function issuerReadinessMarkup(r){
 const c=researchComposite(r);
 const gaps=c.pillars.filter(p=>p.available<p.max)
  .sort((a,b)=>(b.max-b.available)-(a.max-a.available));
 const older=c.pillars.filter(p=>p.asof&&p.asof<r.price_date);
 return '<div class="u-issuer-readiness"><h3>이 종목의 자료 확보 현황</h3>'+
  '<p class="u-source">가격 기준 '+esc(r.price_date)+' · 확보 배점 '+c.available+
  '/100 · 추가 자료가 필요한 배점 '+c.missing+'/100</p>'+
  (gaps.length?'<ul class="u-fcf-stock-list">'+gaps.map(p=>
   '<li><b>'+esc(p.label)+'</b><span>확보 '+p.available+'/'+p.max+
   ' · 미확보 '+(p.max-p.available)+'점 배점</span></li>').join('')+'</ul>':
   '<p class="u-source">5개 영역의 배점 자료가 모두 확보됐습니다. 원천·기업행위·과거 시점 검증은 별도 진행 중입니다.</p>')+
  (older.length?'<p class="u-source">이전 자료로 유지 중: '+older.map(p=>
   esc(p.label)+' ('+esc(p.asof)+')').join(' · ')+
   '. 새 자료 확인 시 갱신됩니다.</p>':'')+
  '<p class="u-source">미확보 배점은 점수 감점이나 예상 상승 여력이 아닙니다. 자료가 추가되면 점수와 순위는 오르거나 내릴 수 있습니다.</p></div>';
}
function subfactorHistoryMarkup(key,r){
 const specs=FISCAL_SUBFACTORS[key],snapshots=r?.v3_last_known_components?.[key];
 if(!specs||!snapshots||!Object.keys(snapshots).length)return '';
 const fmt=n=>n.toFixed(1).replace(/\.0$/,'');
 const lines=Object.entries(specs).flatMap(([name,[label,max]])=>{
  const e=snapshots[name];
  if(!e||!validDate(e.asof)||!finite(e.points)||e.available!==max)return [];
  const prior=r?.v3_component_history?.[key]?.[name];
  const previous=Array.isArray(prior)&&prior.length?prior[prior.length-1]:null;
  const update=(r?.price_date&&e.asof<r.price_date)?' · 최근 수집 미갱신':'';
  const older=previous?' · 직전 '+esc(previous.asof)+' '+fmt(previous.points)+'점':'';
  return ['<li><span>'+esc(label)+'</span><b>'+fmt(e.points)+' / '+max+'점</b>'+
   '<small>'+esc(e.asof)+' 관측'+update+older+'</small></li>'];
 });
 if(!lines.length)return '';
 return '<div class="u-subfactor-history"><p><b>세부항목별 마지막 유효 관측값</b> ('+
  lines.length+'개) · 개별 원천 기준일을 유지합니다.</p><ul>'+lines.join('')+'</ul>'+
  '<p>미갱신은 수집 실패를 의미하지 않으며 원인은 별도 감사 대상입니다. 이력은 최대 직전 3회 관측까지 유지합니다. 영역 합계·순위에 별도 가산하지 않습니다.</p></div>';
}
function scorePillarExplanation(p,r){
 const number=v=>finite(v)?v.toFixed(1).replace(/\.0$/,''):'미산출';
 const budget=p.max-p.available;
 const inherited=p.carried?
  '<p class="u-last-factor"><b>'+esc(p.asof)+' 관측 점수 유지</b> · '+
  '이 영역의 '+number(p.candidate)+'/'+p.available+
  '점은 표시 합계와 개인 연구 순위에 포함됩니다. '+
  '오늘 새로 계산된 점수가 아니며, 특히 주가 민감 가치항목은 재산출 전입니다.</p>':'';
 const intro='<p><b>잠정 획득 '+number(p.candidate)+'점</b> · 산출 가능 '+
  p.available+'/'+p.max+'점 · 미확보 '+budget+'점. '+
  (p.asof?'근거 기준일 '+esc(p.asof)+' · ':'')+
  '미확보는 0점으로 계산한 것이 아닙니다.</p>'+inherited;
 const notes={
  momentum:'시장 대비 20·60거래일 상대강도 관찰입니다. 수정주가·기업행위와 시장 기준일의 독립 검증 전으로 확정 추세점수가 아닙니다.',
  valuation:'원래 관측 기준일의 재무·주가 및 FCF수익률 연구값을 참고합니다. 확정 PBR 점수나 적정주가 판단으로 사용하지 않습니다.',
  financial:'연간 재무 관측과 3개년 FCF/자산 연구값을 참고합니다. 과거 공시 정정·효력시점(PIT)은 독립 검증 전입니다.',
  technical:'일봉·주봉 패턴, RSI, 120주선 및 거래량 확인 항목입니다. 형상 후보는 확정 매수 신호가 아닙니다.',
  volume:'거래량과 OBV로 계산한 연구값입니다. 기관·외국인 실제 순매수나 매집을 증명하지 않습니다.'
 };
 let detail='';
 if(p.key==='technical'&&r)detail=technicalParts(r);
 if(p.key==='volume'&&r)detail=volumeParts(r);
 if(p.key==='momentum'&&r&&momentumEvidence(r)){
  const z=r.v3_momentum_research.momentum_parts;
  const one=(label,v)=>'<div><span>'+label+'</span><b>'+
   (finite(v)?v.toFixed(1):'—')+' / 4</b></div>';
  detail='<div class="u-parts u-score-breakdown" aria-label="모멘텀 관측 근거">'+
   one('시장 대비 20일',z.relative20)+one('시장 대비 60일',z.relative60)+
   '</div><p>그 외 상승일 비율·추세 지속성·반전 폭 등은 관측 배점에 포함하지 않았습니다.</p>';
 }
 if((p.key==='financial'||p.key==='valuation')&&r){
  const f=fcfEvidenceStatus(r);
  if(f.state==='matched'){
   const metric=p.key==='financial'?f.threeYear:f.latest;
   const label=p.key==='financial'?'3개년 FCF/자산':'최근 연도 FCF수익률';
   const raw=metric.state==='not_observed'?'미산출':pct(metric.raw);
   detail='<p>'+label+' 원자료 '+raw+
    (metric.state==='provisional'?' · FCF 잠정 '+metric.points.toFixed(1)+
     '/'+metric.max+'점 · 동시장 표본 '+metric.peers+'개':' · 별도 배점 미확정')+
    '</p>';
  }
  const history=subfactorHistoryMarkup(p.key,r);
  detail+=history||'<p>세부항목별 과거 관측값이 아직 없어 영역 잠정 합계만 표시합니다. 하단 FCF 확보 상태에서 공시 계정별 자료를 확인할 수 있습니다.</p>';
 }
 return '<div class="u-score-row-explain">'+intro+detail+
  '<p class="u-score-explain-caution">'+esc(notes[p.key]||'미검증 연구값')+
  '</p></div>';
}
function comparisonPillarBars(a,b,names,comparable){
 const labels=[['momentum','모멘텀'],['valuation','밸류'],
  ['financial','재무'],['technical','차트'],['volume','거래량']];
 const maps=[a,b].map(c=>new Map((c?.pillars||[]).map(p=>[p.key,p])));
 const valid=(p,key)=>p&&p.max===WEIGHTS[key]&&
  Number.isInteger(p.available)&&p.available>=0&&p.available<=p.max&&
  (p.candidate===null||(finite(p.candidate)&&p.candidate>=0&&
  p.candidate<=p.available));
 if(maps.some(m=>m.size!==5)||labels.some(([key])=>
    maps.some(m=>!valid(m.get(key),key))))
  return '<p class="u-muted">비교 영역별 자료를 재검증 중입니다.</p>';
 const num=v=>finite(v)?v.toFixed(1).replace(/\.0$/,''):'미산출';
 const width=(v,max)=>(v/max*100).toFixed(2);
 const side=(p,name)=>{
  const scored=p.candidate??0,missing=p.max-p.available;
  const description=esc(name)+' · '+num(p.candidate)+' / '+p.max+
   '점 · 산출 가능 '+p.available+'점 · 미확보 '+missing+'점'+
   (p.asof?' · '+p.asof+' 기준':'');
  return '<div class="u-compare-factor" aria-label="'+description+'">'+
   '<div class="u-compare-factor-value"><strong>'+num(p.candidate)+'</strong>'+
   '<small>/'+p.max+' · 확보 '+p.available+' · 미확보 '+missing+
   (p.asof?' · '+esc(p.asof)+(p.carried?' 유지':''):'')+'</small></div>'+
   '<div class="u-compare-meter" aria-hidden="true">'+
   '<span class="u-score-earned" data-score-width="'+width(scored,p.max)+'"></span>'+
   '<span class="u-score-observed" data-score-width="'+width(p.available-scored,p.max)+'"></span>'+
   '<span class="u-score-missing" data-score-width="'+width(missing,p.max)+'"></span>'+
   '</div></div>';
 };
 return '<section class="u-compare-bars" aria-label="두 종목 5개 영역 배점 비교">'+
  '<h3>5영역 점수 분포 <small>파랑: 잠정 획득 · 연파랑: 확보 배점 중 미획득 · 사선: 미확보</small></h3>'+
  '<p class="u-compare-bars-warning">'+(comparable?
   '관측 기준일과 영역별 확보 배점이 같습니다. 실제 투자성과는 검증되지 않았습니다.':
   '기준일 또는 영역별 확보 배점이 다릅니다. 점수의 직접적인 우열 비교는 불가합니다.')+'</p>'+
  '<div class="u-compare-bars-head"><span>영역</span><strong>'+
   esc(names[0])+'</strong><strong>'+esc(names[1])+'</strong></div>'+
  labels.map(([key,label])=>'<div class="u-compare-bars-row" data-pillar="'+key+'">'+
   '<strong>'+label+'</strong>'+side(maps[0].get(key),names[0])+
   side(maps[1].get(key),names[1])+'</div>').join('')+
  '<p class="u-compare-bars-foot">미산출은 0점이 아니며, 채워진 색도 공식 검증 점수가 아닌 연구 관측치입니다.</p>'+
  '</section>';
}
function scoreOverviewMarkup(c,normalized,r){
 if(!c||!Array.isArray(c.pillars)||c.pillars.length!==5||
    !Number.isInteger(c.available)||c.available<0||c.available>100||
    !Number.isInteger(c.missing)||c.missing!==100-c.available||
    (c.earned!==null&&(!finite(c.earned)||c.earned<0||
      c.earned>c.available)))return '<p class="u-muted">영역별 점수 데이터 재검증 중</p>';
 const earned=c.earned??0, headroom=c.available-earned;
 const percent=(value,maximum)=>(value/maximum*100).toFixed(2);
 const number=value=>finite(value)?value.toFixed(1).replace(/\.0$/,''):'—';
 const labels=[['momentum','모멘텀'],['valuation','밸류'],
  ['financial','재무'],['technical','차트'],['volume','거래량']];
 const map=new Map(c.pillars.map(p=>[p.key,p]));
 if(map.size!==5||labels.some(([key])=>!map.has(key)))return '<p class="u-muted">영역별 점수 데이터 재검증 중</p>';
 const rows=labels.map(([key,label])=>{
  const p=map.get(key),max=WEIGHTS[key];
  if(p.max!==max||!Number.isInteger(p.available)||p.available<0||
     p.available>max||
     (p.candidate!==null&&(!finite(p.candidate)||p.candidate<0||
       p.candidate>p.available)))return null;
  const scored=p.candidate??0;
  const when=p.asof?' · '+p.asof+' 기준'+(p.carried?' (이전 관측 유지)':''):'';
  const status=p.available===0?'자료 미확보':
   '확보 '+p.available+'/'+max+' · 미확보 '+(max-p.available)+when;
  return '<details class="u-score-row" data-pillar="'+key+'" aria-label="'+
   esc(label)+' 잠정 '+number(p.candidate)+'점 / '+max+'점, '+esc(status)+'">'+
   '<summary class="u-score-row-main"><span class="u-score-row-label">'+esc(label)+'</span>'+
   '<span class="u-score-track" aria-hidden="true">'+
   '<span class="u-score-earned" data-score-width="'+percent(scored,max)+'"></span>'+
   '<span class="u-score-observed" data-score-width="'+percent(p.available-scored,max)+'"></span>'+
   '<span class="u-score-missing" data-score-width="'+percent(max-p.available,max)+'"></span></span>'+
   '<span class="u-score-row-value"><strong>'+number(p.candidate)+'</strong>'+
   '<small>/'+max+'</small></span>'+
   '<small class="u-score-row-status">'+esc(status)+'</small></summary>'+
   scorePillarExplanation(p,r)+'</details>';
 });
 if(rows.some(v=>v===null)||
    c.pillars.reduce((sum,p)=>sum+p.available,0)!==c.available||
    (c.earned===null)!==(c.available===0)||
    Math.abs(c.pillars.reduce((sum,p)=>sum+(p.candidate??0),0)-earned)>0.000101||
    normalized!==personalHundredFromComposite(c))
   return '<p class="u-muted">영역별 점수 데이터 재검증 중</p>';
 return '<section class="u-score-visual" aria-labelledby="detailScoreTitle">'+
  '<div class="u-score-visual-heading"><h3 id="detailScoreTitle">5영역 잠정 점수</h3>'+
  '<span>총 100점 배점 · 미인증 · 막대를 눌러서 근거 보기</span></div>'+
  '<div class="u-score-visual-body">'+
  '<div class="u-score-total"><div class="u-score-total-max"><strong>100</strong><small>만점</small></div>'+
  '<div class="u-score-total-track" role="img" aria-label="100점 만점 중 잠정 획득 '+
   number(c.earned)+'점, 산출 가능 배점 '+c.available+'점, 미확보 '+c.missing+'점">'+
   '<span class="u-score-earned" data-score-height="'+percent(earned,100)+'"></span>'+
   '<span class="u-score-observed" data-score-height="'+percent(headroom,100)+'"></span>'+
   '<span class="u-score-missing" data-score-height="'+percent(c.missing,100)+'"></span>'+
   '<strong class="u-score-total-value">'+number(c.earned)+'</strong></div>'+
  '<strong class="u-score-total-caption">잠정 합계</strong></div>'+
  '<div class="u-score-rows">'+rows.join('')+'</div></div>'+
  '<div class="u-score-legend"><span><i class="u-score-swatch earned"></i>잠정 획득</span>'+
  '<span><i class="u-score-swatch observed"></i>확보 배점 중 미획득</span>'+
  '<span><i class="u-score-swatch missing"></i>자료 미확보</span></div>'+
  '<p class="u-score-disclaimer">잠정 합계 <b>'+
  (c.earned===null?'미산출':number(c.earned)+'점')+'</b> · 산출 가능 '+
  c.available+'/100점 · 미확보 '+c.missing+'/100점. 미확보는 0점이 아닙니다. '+
  (c.stalePillars?' 이전 관측일이 포함된 영역 '+c.stalePillars+
   '개, 가장 오래된 근거 '+esc(c.oldestAsOf)+'. 각 영역의 실제 기준일을 확인하세요.':'')+
  (normalized===null?'개인용 100점 비율 환산은 5영역·75점 이상 관측 시에만 표시합니다.':
   '별도 비율 환산 '+number(normalized)+'/100점은 관측 배점 기준이며 확정 투자점수가 아닙니다.')+
  ' 매수 신호나 수익 예측으로 사용하지 마세요.</p></section>';
}
// The site CSP rejects style attributes in innerHTML. Once mounted, use
// bounded CSSOM property assignments without weakening CSP.
function paintScoreOverview(view){
 for(const node of view.querySelectorAll('.u-score-visual [data-score-width], .u-score-visual [data-score-height], .u-compare-bars [data-score-width]')){
  for(const [attr,property] of [['data-score-width','width'],['data-score-height','height']]){
   const raw=node.getAttribute(attr);
   if(raw===null)continue;
   const n=Number(raw);
   if(finite(n)&&n>=0&&n<=100)node.style[property]=n.toFixed(2)+'%';
  }
 }
}
function matchCompareStock(model,value){
 if(!Array.isArray(model?.rows))return null;
 const token=String(value??'').trim().toLocaleLowerCase('ko-KR');
 if(!token)return null;
 const isCode=/^[0-9]{6}$/.test(token);
 const hits=model.rows.filter(x=>isCode?
  x.r?.code===token:String(x.r?.name||'').trim().toLocaleLowerCase('ko-KR')===token);
 return hits.length===1?hits[0]:null;
}
function resolveComparePair(model,leftValue,rightValue){
 if(!String(leftValue??'').trim())return {ok:false,reason:'left_required'};
 if(!String(rightValue??'').trim())return {ok:false,reason:'right_required'};
 const left=matchCompareStock(model,leftValue);
 if(!left)return {ok:false,reason:'left_not_found'};
 const right=matchCompareStock(model,rightValue);
 if(!right)return {ok:false,reason:'right_not_found'};
 if(left.r.code===right.r.code)return {ok:false,reason:'same_stock'};
 const a=researchComposite(left.r),b=researchComposite(right.r);
 return {ok:true,left,right,a,b,
  sameDate:validDate(left.r.price_date)&&left.r.price_date===right.r.price_date&&
   left.r.price_date===model.date,
  sameCoverage:coverageVector(a.pillars)===coverageVector(b.pillars)};
}
// Compare the available ceilings of ALL five V3 pillars, not just their sum.
// Equal coverage vectors still do not establish source/PIT or return certification.
function coverageVector(pillars){
 return pillars.map(p=>p.available).join('-');
}
function coverageKey(r){
 return coverageVector(pillarState(r));
}
function coverageGroups(model){
 if(!Array.isArray(model?.rows))return [];
 const groups=new Map();
 for(const x of model.rows){
  const pillars=pillarState(x.r);
  const key=coverageVector(pillars);
  const found=groups.get(key);
  if(found){found.count++;continue;}
  const parts=pillars.map(p=>p.available);
  groups.set(key,{key,parts,total:parts.reduce((a,b)=>a+b,0),count:1});
 }
 return [...groups.values()].filter(x=>x.count>=2).sort((a,b)=>
  b.total-a.total||b.count-a.count||a.key.localeCompare(b.key));
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

/* Stability research overlay version 3.
 * Cap risk is capped at 5/100. A SINGLE-DAY Amount remains informational
 * and NEVER changes score. No one-day liquidity proxy can replace a history
 * of actual trading VALUE. Structural stop reference is unverified research.
 * The 5-area V3 original score, volume/OBV, and issuer ledger are untouched.
 */
function stabilityProfile(r,composite){
 const tenths=n=>Math.round((n+Number.EPSILON)*10)/10;
 const cap=finite(r?.market_cap_eok)&&r.market_cap_eok>0?r.market_cap_eok:null;
 const trading=finite(r?.trading_eok)&&r.trading_eok>=0?r.trading_eok:null;
 const capitalDeduction=cap===null?0:tenths(5*Math.max(0,1-cap/5000));
 const liquidityDeduction=0; // actual 20/60/120/240d & 5/10/20/60/120w money series not collected
 const price=finite(r?.price)&&r.price>0?r.price:null;
 const stop=finite(r?.stop_line)&&r.stop_line>0?r.stop_line:null;
 const pct=price!==null&&stop!==null&&stop<price?100*(price-stop)/price:null;
 const distance=pct!==null&&finite(r?.stop_pct)&&Math.abs(r.stop_pct+pct)<=2?
  Math.round(pct*100)/100:null;
 // A nearby stop can be hit by noise. Award only modest points for a
 // usable 6..9% distance; too tight (<3%) and too wide (>15%) lose points.
 let stopAdjustment=0;
 if(distance!==null){
  const d=distance;
  const raw=d<3?-2+2*d/3:
   d<6?2*(d-3)/3:d<=9?2:d<12?2*(12-d)/3:
   d<15?-(d-12)/3:d<25?-1-2*(d-15)/10:-3;
  const techRisk=(finite(r?.price60)&&r.price60>=40)||
   (Array.isArray(r?.risk_flags)&&r.risk_flags.some(f=>typeof f==='string'&&
    ['손절폭 과대','단기 급등','추격 주의','장기 추세 취약'].some(k=>f.includes(k))));
  stopAdjustment=tenths(techRisk?Math.min(raw,-1):raw);
 }
 // Signed deduction: a negative result denotes a small confirmed-shape
 // research bonus, never a promise of execution at the reference line.
 const deduction=tenths(capitalDeduction-stopAdjustment);
 const score=composite?.earned===null||!finite(composite?.earned)?null:
  Math.max(0,Math.min(100,tenths(composite.earned-deduction)));
 const missing=(cap===null?1:0)+(distance===null?1:0);
 const status=distance===null?'손절 구조선 확인 필요':
  distance<3?'손절선 너무 근접 · 노이즈 위험':
  distance>15?'손절폭 과대 · 하락 위험':
  cap!==null&&cap<1000?'소형주 규모 주의 · 구조 참고':
  stopAdjustment>0?'구조 손절거리 상대 양호 · 미검증':
  '구조 손절거리 추가 확인';
 return {capitalEok:cap,tradingEok:trading,capitalDeduction,
  liquidityDeduction,stopAdjustment,stopDistancePct:distance,
  deduction,score,missing,status,turnoverPeriodEvidenceAvailable:false,
  asof:validDate(r?.price_date)?r.price_date:null};
}
function compareStableResearchScores(a,b){
 const ac=researchComposite(a.r),bc=researchComposite(b.r);
 const ap=stabilityProfile(a.r,ac),bp=stabilityProfile(b.r,bc);
 return bc.available-ac.available||
   (bp.score??-1)-(ap.score??-1)||
   ap.missing-bp.missing||
   compareResearchScores(a,b);
}
function rankItems(model,core,tab='discover',market='all',query='',sort='stable',safeOnly=false,top100Only=false,obvOnly=false,coverageOnly='all'){
 if(!model?.rows||!core)return [];
 const ordered=model.rows.slice().sort(sort==='stable'?compareStableResearchScores:compareResearchScores);
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
  const personal100=personalHundredFromComposite(composite);
  const stability=stabilityProfile(x.r,composite);
  const priority=signals.reduce((acc,z)=>acc+(z.kind==='pattern'?
    (z.timeframe==='weekly'?4:3)+(z.stage==='돌파 관측'?1:0):1),0);
  return {...x,daily,weekly,signals,patterns,rsi,risk,tech,priority,composite,
    candidatePosition:candidatePositions.get(x.r.code)??null,personal100,stability,
    coverageKey:coverageVector(composite.pillars)};
 }).filter(x=>{
  const r=x.r;
  // Direct ticker lookup is always global. Never suppress a ticker because
  // its provisional research score is missing or an optional tab is selected.
  if(search)return (r.name+' '+r.code+' '+(r.sector||''))
    .toLocaleLowerCase('ko-KR').includes(search);
  if(obvOnly&&!obvLagCandidate(r))return false;
  if(coverageOnly!=='all'&&x.coverageKey!==coverageOnly)return false;
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
  if(sort==='stable')return b.composite.available-a.composite.available||
    (b.stability.score??-1)-(a.stability.score??-1)||
    a.stability.missing-b.stability.missing||compareResearchScores(a,b);
  if(sort==='score')return compareResearchScores(a,b);
  if(sort==='personal')return (b.personal100??-1)-(a.personal100??-1)||
    compareResearchScores(a,b);
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
function volumeStatus(pillar){
 if(!pillar||pillar.key!=='volume'||pillar.max!==WEIGHTS.volume)
  return '거래량·OBV 기준 확인 불가';
 return pillar.candidate===null
  ?'신규 MA20·60일선 교차점/240일선 근거 또는 거래량 자료가 미확보입니다. 구형 OBV 상승 6점은 중단했으며 결측은 0점이 아닙니다.'
  :'거래량/OBV 괴리 및 최근20일 골든크로스의 240일선 대비 ±12%를 연구용으로 평가합니다. 현재 확보 배점 '+pillar.available+'/20, 실전 인증 전입니다.';
}
function volumeParts(r){
 const v=observedVolumeEvidence(r);
 const parts=v?r.v3_volume_observed.subpoints:{};
 return '<div class="u-parts u-volume-parts" aria-label="거래량·OBV 20점 관측 근거">'+
  VOLUME_COMPONENTS.map(([label,max,key])=>
   '<div class="u-factor-unverified"><span>'+esc(label)+'</span>'+
   '<b>'+(v&&finite(parts[key])?parts[key].toFixed(1):'—')+' / '+max+
   ' <em class="u-factor-mark">'+(v&&finite(parts[key])?'잠정':'미확보')+
   '</em></b></div>').join('')+'</div>';
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
function quantSourceQualityView(report, marketDate){
 if(!report||!['exploratory','insufficient'].includes(report.status)||
    report.asof!==marketDate)return null;
 const q=report.source_quality_audit;
 if(!q||!Number.isInteger(q.requested_symbols)||q.requested_symbols<1||
    q.requested_symbols>120||!Number.isInteger(q.valid_ohlcv_symbols)||
    !Number.isInteger(q.excluded_symbols)||q.valid_ohlcv_symbols<0||
    q.excluded_symbols<0||
    q.valid_ohlcv_symbols+q.excluded_symbols!==q.requested_symbols||
    q.all_exclusions_listed!==true||
    q.official_adjusted_prices_certified!==false||
    q.delisted_historical_universe_included!==false||
    !Array.isArray(report.missing)||report.missing.length!==q.excluded_symbols||
    !q.excluded_reason_counts||typeof q.excluded_reason_counts!=='object'||
    Array.isArray(q.excluded_reason_counts))return null;
 const labels={
  history_file_missing:'이력 파일 부재',
  ohlc_range_invalid:'가격 범위 불일치',
  nonpositive_price_or_negative_volume:'비정상 가격·거래량',
  history_too_short:'분석기간 부족',
  source_date_mismatch:'기준일 불일치',
  duplicate_dates:'거래일 중복',
  incomplete_or_invalid_fields:'필수 수치 결측·오류',
  other_unverified_source_error:'기타 미검증'
 };
 const counts=q.excluded_reason_counts;
 const keys=Object.keys(counts);
 if(keys.some(k=>!Object.prototype.hasOwnProperty.call(labels,k)||
     !Number.isInteger(counts[k])||counts[k]<0)||
    keys.reduce((sum,k)=>sum+counts[k],0)!==q.excluded_symbols||
    report.missing.some(m=>!m||!/^[0-9]{6}$/.test(String(m.code))||
     !Object.prototype.hasOwnProperty.call(labels,m.reason_code)))return null;
 const missingCodes=report.missing.map(m=>String(m.code));
 if(new Set(missingCodes).size!==missingCodes.length)return null;
 const tally={};
 report.missing.forEach(m=>{tally[m.reason_code]=(tally[m.reason_code]||0)+1;});
 if(keys.some(k=>tally[k]!==counts[k])||
    Object.keys(tally).some(k=>!Object.prototype.hasOwnProperty.call(counts,k)))
    return null;
 const hasTailAudit=Object.prototype.hasOwnProperty.call(q,'audited_vendor_tail_diagnostics')||
   Object.prototype.hasOwnProperty.call(q,'unverified_tail_650_candidates');
 let tailNote='',queueHTML='';
 if(hasTailAudit){
  const audited=q.audited_vendor_tail_diagnostics;
  const candidates=q.unverified_tail_650_candidates;
  const diagnosed=report.missing.filter(m=>m.tail_diagnostic!==undefined);
  if(!Number.isInteger(audited)||!Number.isInteger(candidates)||
     audited<0||candidates<0||candidates>audited||audited>q.excluded_symbols||
     diagnosed.length!==audited)return null;
  for(const item of diagnosed){
   const z=item.tail_diagnostic;
   if(!z||item.error_provenance!=='matched_same_day_collector'||
      !Number.isInteger(z.suspect_bar_count)||z.suspect_bar_count<1||
      !Number.isInteger(z.trailing_valid_sessions)||z.trailing_valid_sessions<0||
      typeof z.last_suspect_date!=='string'||!validDate(z.last_suspect_date)||
      z.last_suspect_date>marketDate||
      z.research_only!==true||z.official_adjustment_certified!==false||
      z.trailing_650_session_candidate!==(z.trailing_valid_sessions>=650))
      return null;
  }
  if(diagnosed.filter(m=>m.tail_diagnostic.trailing_650_session_candidate).length!==
     candidates)return null;
  tailNote=' · 원천 불량구간 진단 '+audited+'종목 중 수집된 정상 봉 연속 650개 이상 '+
   candidates+'종목 (공식 교차검증 전 복구 후보이며 점수 미반영)';
  if(candidates>0){
   const queue=diagnosed.filter(m=>m.tail_diagnostic.trailing_650_session_candidate)
    .sort((a,b)=>b.tail_diagnostic.trailing_valid_sessions-
      a.tail_diagnostic.trailing_valid_sessions||String(a.code).localeCompare(String(b.code)));
   queueHTML='<details class="u-quant-queue"><summary>공식 교차검증 우선 후보 '+
    candidates+'종목 · 개별 목록 보기</summary>'+
    '<p class="u-source">오류 이후 수집된 봉을 기준으로 정렬했습니다. 휴장·거래정지·누락 거래일의 연속성은 검증하지 않았습니다. '+
    '정규장·수정주가·기업행위 비교 전까지 이 종목들은 퀀트 점수와 수익률 계산에서 제외됩니다.</p>'+
    '<div class="u-scroller"><table><thead><tr><th>종목코드</th>'+
    '<th>제외 원인</th><th>마지막 의심 날짜</th><th>이후 수집된 봉</th></tr></thead><tbody>'+
    queue.map(m=>'<tr><th>'+esc(m.code)+'</th><td>'+esc(labels[m.reason_code])+
      '</td><td>'+esc(m.tail_diagnostic.last_suspect_date)+'</td><td>'+
      m.tail_diagnostic.trailing_valid_sessions+'개</td></tr>').join('')+
    '</tbody></table></div></details>';
  }
 }
 const grouped=keys.filter(k=>counts[k]>0)
  .map(k=>labels[k]+' '+counts[k]+'종목').join(' · ');
 return {
  summary:'9년 퀀트 가격 이력 '+q.valid_ohlcv_symbols+'/'+
   q.requested_symbols+'종목 확보 (제외 '+q.excluded_symbols+')',
  text:'자료 기준 '+marketDate+' · '+(grouped||'제외 종목 없음')+
   tailNote+'. 연구용 원천 품질 집계이며 오류 가격을 임의 보정하지 않습니다. '+
   '공식 정규장·수정주가·기업행위와 과거 상장폐지 종목 구성은 독립 인증 전입니다.',
  queueHTML
 };
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
 const six=report.six_pillar_reference_counts_this_cycle;
 const keys=['ias','technical','financial','valuation','industry','momentum'];
 const validSix=six&&keys.every(k=>Number.isInteger(six[k])&&six[k]>=0&&
   six[k]<=report.processed_this_cycle);
 const sixText=validSix?
  ' · 6영역 참고관측(확정점수 아님): 공식 투자자별 수급 '+six.ias+
  ' / 기술 '+six.technical+' / 4년재무 '+six.financial+
  ' / PBR추정 '+six.valuation+' / 업종명 '+six.industry+
  ' / 모멘텀 '+six.momentum:'';
 return ' · V3 주간 순환 연구 '+report.processed_this_cycle+'/'+
  report.listed_total+'종목 · 자료기준 '+report.market_asof+
  (p===null?'':' · 기술점수 근거 '+p+'종목')+sixText+
  ' (기존 원본 재계산, 별도 실시간 API 수집 아님)';
}
function realSourceCoverageText(report,marketDate){
 if(!report||report.schema!=='v3-real-saved-source-six-pillar-coverage-0.1'||
    !validDate(marketDate)||report.market_asof!==marketDate||
    !Number.isInteger(report.listed_total)||report.listed_total<1||
    report.buy_recommendations_authorized!==false||
    report.investment_grade_scores_confirmed!==false||
    report.six_pillar_official_complete_100pt_issuer_count!==0||
    report.official_investor_IAS_source_issuer_count!==0||
    report.industry_scored_rotation_issuer_count!==0)return '';
 const n=report.six_pillar_numeric_provisional_issuer_counts;
 const keys=['ias','technical','financial','valuation','industry','momentum'];
 if(!n||keys.some(k=>!Number.isInteger(n[k])||n[k]<0||
                    n[k]>report.listed_total)||
    n.ias!==0||n.industry!==0||
    !Number.isInteger(report.four_numerical_areas_same_date_overlap_count)||
    report.four_numerical_areas_same_date_overlap_count<0||
    report.four_numerical_areas_same_date_overlap_count>
      Math.min(n.technical,n.financial,n.valuation,n.momentum))return '';
 return ' · 출처기준 6영역 계산가능 연구점수 (확정점수 아님): 공식 투자자별 수급 '+n.ias+
   ' / 기술 '+n.technical+' / 재무 '+n.financial+
   ' / 가치 '+n.valuation+' / 산업 '+n.industry+
   ' / 모멘텀 '+n.momentum+'종목'+
   ' · 4영역 데이터 검증 시험군 '+report.four_numerical_areas_same_date_overlap_count+'종목';
}
function actualCashflowCoverageText(report,marketDate,listed){
 // Separate actual DART receipts from any unverified financial 15-point score.
 // A missing, future, mismatched or untrusted derived file is not displayed.
 if(!report||report.schema!=='v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1'||
    !validDate(marketDate)||report.asof!==marketDate||
    !Number.isInteger(listed)||listed<1||report.listed_total!==listed||
    report.source_same_filing_scope_receipt_checked!==true||
    report.hist_PIT_verified!==false||
    report.four_year_financial15_complete!==false||
    report.valuation15_complete!==false||
    report.joined_to_main_v3_ranking!==false||
    report.buy_recommendations_authorized!==false||
    report.six_pillar_confirmed_100pt!==null||
    !Number.isInteger(report.minimum_same_market_peers)||
    report.minimum_same_market_peers<30||
    report.financial_fcf_weight_available_max!==4||
    report.valuation_fcf_weight_available_max!==5)return '';
 const counts=[
  report.real_fcf_assets_three_year_raw_issuer_count,
  report.real_latest_fcf_yield_raw_issuer_count,
  report.financial_fcf4_research_candidate_issuer_count,
  report.valuation_fcf5_research_candidate_issuer_count];
 if(counts.some(n=>!Number.isInteger(n)||n<0||n>listed)||
    counts[2]>counts[0]||counts[3]>counts[1]||
    !Array.isArray(report.rows)||report.rows.length>listed)return '';
 return ' · 공식 DART 실제 FCF 3년 자료 '+counts[0]+
  '종목 / 최근연도 FCF수익률 원천 '+counts[1]+
  '종목 · 재무 FCF 4점 잠정계산 '+counts[2]+
  '종목 / 가치 FCF 5점 잠정계산 '+counts[3]+
  '종목 (연결/별도·동일 접수번호 검증, PIT·투자용 확정점수 아님)';
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
function applyBulkFinancialPreview(model,report){
 const newBalanceFactors=report?.financial_partial_max_possible===16&&
   report.five_factor_financial_budget===20&&
   report.balance_factor_peer_limitations===
     'KOSPI_KOSDAQ_only_not_sector_neutral_unverified_research'&&
   Number.isInteger(report.companies_with_computable_liabilities_to_assets_scores)&&
   Number.isInteger(report.companies_with_computable_operating_roa_scores)&&
   report.companies_with_computable_liabilities_to_assets_scores>=0&&
   report.companies_with_computable_operating_roa_scores>=0&&
   report.companies_with_computable_liabilities_to_assets_scores<=report.listed_total&&
   report.companies_with_computable_operating_roa_scores<=report.listed_total;
 const previousFinancial=report?.financial_partial_max_possible===8&&
   report.five_factor_financial_budget===undefined&&
   report.balance_factor_peer_limitations===undefined&&
   report.companies_with_computable_liabilities_to_assets_scores===undefined&&
   report.companies_with_computable_operating_roa_scores===undefined;
 const newSalesFactor=report?.valuation_partial_max_possible===15&&
   report.five_factor_valuation_budget===20&&
   Number.isInteger(report.companies_with_computable_sales_to_cap_scores);
 const previousReference=report?.valuation_partial_max_possible===10&&
   report.five_factor_valuation_budget===undefined&&
   report.companies_with_computable_sales_to_cap_scores===undefined;
 if(!model||!Array.isArray(model.rows)||!validDate(model.date)||
    !report||report.schema!=='v3-dart-multi-year-research-parts-0.1'||
    report.asof!==model.date||report.historical_PIT_certified!==false||
    report.fully_verified_six_pillar_count!==0||
    report.all_scores_provisional_unverified!==true||
    report.model_financial_full_points!==15||
    report.model_valuation_full_points!==15||
    !(newBalanceFactors||previousFinancial)||
    !(newSalesFactor||previousReference)||
    !Array.isArray(report.rows)||
    !Number.isInteger(report.listed_total)||report.listed_total<model.rows.length||
    report.rows.length>report.listed_total)return 0;
 const index=new Map();
 for(const row of report.rows){
  if(!row||!TODAY_CODE.test(row.code)||!validDate(row.asof)||
     row.asof!==model.date||
     !['KOSPI','KOSDAQ'].includes(row.market)||
     row.confirmed_v3_score!==null||row.scoring_certified!==false||
     !Number.isInteger(row.financial_available_max)||
     !(newBalanceFactors?[0,4,8,12,16]:[0,4,8])
       .includes(row.financial_available_max)||
     !Number.isInteger(row.valuation_available_max)||
     !(newSalesFactor?[0,5,10,15]:[0,5,10]).includes(row.valuation_available_max)||
     !finite(row.combined_provisional_points)||
     row.combined_available_max!==
        row.financial_available_max+row.valuation_available_max||
     row.combined_provisional_points<0||
     row.combined_provisional_points>row.combined_available_max||
     index.has(row.code))return 0;
  for(const [p,max] of [
    ['financial_candidate_points','financial_available_max'],
    ['valuation_candidate_points','valuation_available_max']]){
   if(row[max]===0&&row[p]!==null||
      row[max]>0&&(!finite(row[p])||row[p]<0||row[p]>row[max]))return 0;
  }
  index.set(row.code,row);
 }
 let accepted=0;
 for(const x of model.rows){
  const r=x.r,ref=index.get(r.code);
  if(!ref||ref.market!==r.market||r.price_date!==model.date)continue;
  r.v3_dart_bulk_provisional=ref;
  accepted++;
 }
 return accepted;
}
function applyActualCashflowPreview(model,report){
 // All-or-nothing source audit. Each scored item must be genuine same-day,
 // source-receipt-matched, market-specific, peer>=30, and 0..4/5 points.
 // This is research-only: no release flags, orders, or official PIT claims.
 if(!model||!Array.isArray(model.rows)||!validDate(model.date)||
    !report||report.schema!=='v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1'||
    report.asof!==model.date||report.listed_total!==model.marketEligible||
    report.source_same_filing_scope_receipt_checked!==true||
    report.raw_source_rows_stored!==false||
    report.hist_PIT_verified!==false||
    report.four_year_financial15_complete!==false||
    report.valuation15_complete!==false||
    report.joined_to_main_v3_ranking!==false||
    report.buy_recommendations_authorized!==false||
    report.six_pillar_confirmed_100pt!==null||
    report.financial_fcf_weight_available_max!==4||
    report.valuation_fcf_weight_available_max!==5||
    !Number.isInteger(report.minimum_same_market_peers)||
    report.minimum_same_market_peers<30||
    !Array.isArray(report.rows)||report.rows.length>report.listed_total)
    return 0;
 const rows=new Map();let rawFin=0,rawVal=0,fin=0,val=0;
 for(const r of report.rows){
  if(!r||!TODAY_CODE.test(r.code)||rows.has(r.code)||
     r.asof!==model.date||!['KOSPI','KOSDAQ'].includes(r.market)||
     r.scoring_certified!==false||r.historical_PIT_certified!==false||
     r.strict_receipt_scope_matched!==true||
     !Number.isInteger(r.financial_fcf_peer_count)||
     !Number.isInteger(r.valuation_fcf_peer_count)||
     r.financial_fcf_peer_count<0||r.financial_fcf_peer_count>report.listed_total||
     r.valuation_fcf_peer_count<0||r.valuation_fcf_peer_count>report.listed_total)
     return 0;
  const metrics=[
   [r.raw_observed_financial_fcf_assets_3y_pct,
    r.financial_fcf_assets_candidate_points,
    r.financial_fcf_assets_available_max,4,r.financial_fcf_peer_count],
   [r.raw_observed_valuation_fcf_yield_pct,
    r.valuation_fcf_yield_candidate_points,
    r.valuation_fcf_yield_available_max,5,r.valuation_fcf_peer_count]];
  for(const [raw,points,max,weight,peers] of metrics){
   if(raw!==null&&!finite(raw))return 0;
   if(max!==0&&max!==weight)return 0;
   if(max===weight&&(raw===null||!finite(points)||points<0||
      points>weight||peers<report.minimum_same_market_peers))return 0;
   if(max===0&&points!==null)return 0;
   if(max===0&&raw!==null&&peers>=report.minimum_same_market_peers)
      return 0;
  }
  rawFin+=Number(metrics[0][0]!==null);
  rawVal+=Number(metrics[1][0]!==null);
  fin+=Number(metrics[0][2]===4);
  val+=Number(metrics[1][2]===5);
  rows.set(r.code,r);
 }
 if(rawFin!==report.real_fcf_assets_three_year_raw_issuer_count||
    rawVal!==report.real_latest_fcf_yield_raw_issuer_count||
    fin!==report.financial_fcf4_research_candidate_issuer_count||
    val!==report.valuation_fcf5_research_candidate_issuer_count)return 0;
 let attached=0;
 for(const x of model.rows){
  const r=rows.get(x.r.code);
  if(r&&r.market===x.r.market&&x.r.price_date===model.date){
   x.r.v3_dart_fcf_research=r;attached++;
  }
 }
 return attached;
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
// Observed sample comparison, never a stock-specific investment rank.
function industryPriceEvidenceView(raw,asof,listed){
 if(!raw||raw.schema!=='v3-current-company-industry-relative-strength-observed-v1'||
    raw.asof!==asof||!validDate(asof)||raw.listed_total!==listed||
    !Number.isInteger(listed)||listed<100||
    ((raw.classified_company_count===undefined)!==
      (raw.unclassified_company_count===undefined))||
    raw.historical_krx_sector_membership_pit_verified!==false||
    raw.official_industry_15_points_authorized!==false||
    raw.investment_grade_100point_scores_authorized!==false||
    raw.independently_verified_benchmark_session_or_adjusted_prices!==false||
    raw.new_provider_calls!==0||
    !Array.isArray(raw.groups)||raw.groups_total!==raw.groups.length||
    raw.groups.length>300||
    !raw.source_sha256||Object.keys(raw.source_sha256).length!==3||
    Object.values(raw.source_sha256).some(v=>typeof v!=='string'||!/^[a-f0-9]{64}$/.test(v)))
    return null;
 const classified=raw.classified_company_count===undefined?
   listed:raw.classified_company_count;
 const unclassified=raw.unclassified_company_count===undefined?
   0:raw.unclassified_company_count;
 if(!Number.isInteger(classified)||!Number.isInteger(unclassified)||
    classified<Math.ceil(listed*.99)||unclassified<0||
    classified+unclassified!==listed)return null;
 let total=0,paired=0,watch=0,observed120=0,pricedTotal=0;
 const seen=new Set(),rows=[],byKey=new Map();
 const labels=['insufficient_sample','early_relative_recovery_watch',
               'relative20_extension_caution','no_early_recovery_pattern_observed'];
 for(const g of raw.groups){
   if(!g||!['KOSPI','KOSDAQ'].includes(g.market)||
      typeof g.current_dart_industry_prefix_2!=='string'||
      !/^[0-9]{2}$/.test(g.current_dart_industry_prefix_2)||
      !Number.isInteger(g.current_listed_issuers)||
      !Number.isInteger(g.same_day_priced_issuers)||
      !Number.isInteger(g.complete_relative20_60_issuer_count)||
      g.current_listed_issuers<1||
      g.same_day_priced_issuers<g.complete_relative20_60_issuer_count||
      g.complete_relative20_60_issuer_count<0||
      g.same_day_priced_issuers>g.current_listed_issuers||
      g.missing_relative20_60_issuer_count!==
         g.current_listed_issuers-g.complete_relative20_60_issuer_count||
      !labels.includes(g.early_rotation_research_state)||
      g.official_industry_15_points!==null||
      g.current_company_codes_not_historical_krx_sector!==true||
      g.market_benchmark_dates_independently_verified!==false||
      g.prices_corporate_action_adjusted_verified!==false)return null;
   const key=g.market+':'+g.current_dart_industry_prefix_2;
   if(seen.has(key))return null;seen.add(key);
   const n=g.complete_relative20_60_issuer_count;
   if(!finite(g.observed_20_60_coverage)||
      Math.abs(g.observed_20_60_coverage-n/g.current_listed_issuers)>0.000002)return null;
   const metrics=[g.median_market_relative_20pct_sample,
                  g.median_market_relative_60pct_sample];
   const breadth=[g.positive_relative20_breadth_observed_only,
                  g.positive_relative20_breadth_all_lower_bound,
                  g.positive_relative20_breadth_all_upper_bound];
   if(n<3){
     if(metrics.some(v=>v!==null)||breadth.some(v=>v!==null)||
        g.positive_relative20_issuers_sample!==null)return null;
   }else{
     if(metrics.some(v=>!finite(v)||v< -100||v>1000)||
        breadth.some(v=>!finite(v)||v<0||v>1)||
        !Number.isInteger(g.positive_relative20_issuers_sample)||
        g.positive_relative20_issuers_sample<0||
        g.positive_relative20_issuers_sample>n||
        Math.abs(breadth[0]-g.positive_relative20_issuers_sample/n)>0.000002||
        Math.abs(breadth[1]-g.positive_relative20_issuers_sample/g.current_listed_issuers)>0.000002||
        Math.abs(breadth[2]-
          (g.positive_relative20_issuers_sample+g.current_listed_issuers-n)/
            g.current_listed_issuers)>0.000002)return null;
   }
   // Older same-day files have no 120-day fields; leave them explicitly unknown.
   const has120=['relative120_issuer_count','missing_relative120_issuer_count',
     'observed_120_coverage','median_market_relative_120pct_sample']
     .some(k=>Object.prototype.hasOwnProperty.call(g,k));
   let n120=0,m120=null;
   if(has120){
     n120=g.relative120_issuer_count;
     m120=g.median_market_relative_120pct_sample;
     if(!Number.isInteger(n120)||n120<0||n120>g.same_day_priced_issuers||
        g.missing_relative120_issuer_count!==g.current_listed_issuers-n120||
        !finite(g.observed_120_coverage)||
        Math.abs(g.observed_120_coverage-n120/g.current_listed_issuers)>0.000002||
        (n120<3?m120!==null:!finite(m120)||m120< -100||m120>1000))return null;
   }
   observed120+=n120;
   byKey.set(key,{
    market:g.market,code:g.current_dart_industry_prefix_2,
    sample20_60:n, sample120:n120,listed:g.current_listed_issuers,
    m20:metrics[0],m60:metrics[1],m120
   });
   const eligible=n>=10&&n/g.current_listed_issuers>=.75;
   const early=g.early_rotation_research_state==='early_relative_recovery_watch';
   if(early&&(!eligible||metrics[0]<=0||metrics[1]>0||
                breadth[0]<=.5||metrics[0]>=15))return null;
   if(!eligible&&g.early_rotation_research_state!=='insufficient_sample')return null;
   watch+=Number(early);total+=g.current_listed_issuers;paired+=n;
   pricedTotal+=g.same_day_priced_issuers;
   if(early)rows.push({
     market:g.market,code:g.current_dart_industry_prefix_2,
     n,listed:g.current_listed_issuers,m20:metrics[0],m60:metrics[1],
     m120, n120,lower:breadth[1],upper:breadth[2]
   });
 }
 if(total!==classified||paired!==raw.paired_20_60_observations||
    watch!==raw.groups_early_recovery_watch||
    (raw.relative120_observations!==undefined&&
      raw.relative120_observations!==observed120))return null;
 if(raw.unclassified_priced_issuers!==undefined&&
   (!Number.isInteger(raw.unclassified_priced_issuers)||
    raw.unclassified_priced_issuers<0||
    raw.unclassified_priced_issuers>unclassified||
    raw.price_observation_total!==pricedTotal+raw.unclassified_priced_issuers))
   return null;
 rows.sort((a,b)=>b.n-a.n||a.market.localeCompare(b.market)||
           a.code.localeCompare(b.code));
 const strength=v=>finite(v)?((v>0?'+':'')+v.toFixed(1)+'%p'):'미확인';
 const html='<h3>업종 상대강도 회복 관찰 · 동일 시장일</h3>'+
  '<p class="u-muted">현재 상장 '+listed+'개 (업종 확인 '+classified+
  '개 · 업종 미확인 '+unclassified+'개) 중 20·60일 상대강도 동시 확보 '+
  paired+'개 · 관찰 후보 '+watch+'개 업종코드 그룹. 지수 대비 상대강도 표본 중앙값 기준으로 20일 양수·60일 0 이하인 그룹을 연구용으로 추렸습니다. <strong>매수 순위나 산업 15점이 아닙니다.</strong></p>'+
  (rows.length?'<div class="u-scroller"><table><thead><tr>'+
  '<th>시장 · 코드</th><th>관측/전체</th><th>20일 상대강도</th>'+
  '<th>60일 상대강도</th><th>120일 상대강도</th><th>전체 강세비율 가능한 범위</th></tr></thead><tbody>'+
  rows.slice(0,12).map(g=>
   '<tr><th>'+esc(g.market)+' · '+esc(g.code)+'</th><td>'+g.n+'/'+
   g.listed+'</td><td>'+strength(g.m20)+'</td><td>'+strength(g.m60)+
   '</td><td>'+strength(g.m120)+'</td><td>'+(g.lower*100).toFixed(0)+'~'+(g.upper*100).toFixed(0)+
   '%</td></tr>').join('')+'</tbody></table></div>':
   '<p class="u-muted">현재 엄격한 표본 조건을 충족한 상대강도 회복 관찰 후보가 없습니다.</p>')+
  '<p class="u-muted">* 업종은 현재 DART 회사업종코드 2자리 분류입니다. 과거 구성종목, 수정주가 및 지수별 거래일은 별도 인증 전이며 후보 판정은 매매 신호가 아닙니다.</p>';
 return {html,paired,watch,byKey,observed120,classified,unclassified};
}
// The current OpenDART code is an issuer classification, not a KRX
// historical/index sector. Validate the entire issuer census atomically.
function applyIndustryReference(model,raw,groupEvidence){
 if(!model||!Array.isArray(model.rows)||!validDate(model.date)||
    !Number.isInteger(model.marketEligible)||model.marketEligible<100||
    !raw||raw.schema!=='v3-dart-current-listed-industry-code-reference-0.1'||
    raw.asof!==model.date||raw.listed_total!==model.marketEligible||
    !Number.isInteger(raw.observed_industry_codes)||
    !Number.isInteger(raw.missing_industry_codes)||
    !Number.isInteger(raw.not_yet_attempted)||
    raw.observed_industry_codes<0||raw.missing_industry_codes<0||
    raw.not_yet_attempted<0||
    raw.observed_industry_codes+raw.missing_industry_codes+
      raw.not_yet_attempted!==model.marketEligible||
    raw.official_krx_sector_membership_verified!==false||
    raw.past_constituent_membership_pit_verified!==false||
    raw.industry_5433_points_authorized!==false||
    raw.investable_v3_score_certified!==false||
    typeof raw.source_records_digest!=='string'||
    !/^[a-f0-9]{64}$/.test(raw.source_records_digest)||
    !raw.records||typeof raw.records!=='object'||Array.isArray(raw.records)||
    Object.keys(raw.records).length!==raw.observed_industry_codes)return null;
 const groups=groupEvidence?.byKey instanceof Map?groupEvidence.byKey:null;
 const classification=new Map();
 for(const [code,record] of Object.entries(raw.records)){
   if(!TODAY_CODE.test(code)||!record||
      !['KOSPI','KOSDAQ'].includes(record.market)||
      record.status!=='observed'||record.origin!=='OpenDART_company_json'||
      record.historical_pit_verified!==false||
      typeof record.industry_code!=='string'||
      !/^[0-9]{2,8}$/.test(record.industry_code)||
      !validDate(record.checked_on)||
      Date.parse(record.checked_on)>Date.parse(model.date)+2*86400000||
      Date.parse(record.checked_on)<Date.parse(model.date)-45*86400000||
      typeof record.corporation_identity_digest!=='string'||
      !/^[a-f0-9]{64}$/.test(record.corporation_identity_digest))return null;
   classification.set(code,record);
 }
 const updates=[];
 for(const x of model.rows){
   if(!x?.r||!TODAY_CODE.test(x.r.code))return null;
   const record=classification.get(x.r.code);
   // Unknown issuer codes remain unavailable, without hiding other issuers.
   if(!record){
     updates.push([x.r,null]);
     continue;
   }
   if(record.market!==x.r.market)return null;
   const prefix=record.industry_code.slice(0,2);
   updates.push([x.r,{
     code:record.industry_code,prefix,
     name:KSIC2_NAMES[prefix]||null,
     group:groups?.get(record.market+':'+prefix)||null,
     kind:'current_dart_ksic2_not_krx_index_sector'
   }]);
 }
 for(const [row,sector] of updates)row.v3_sector_research=sector;
 return {
   mapped:updates.filter(x=>x[1]!==null).length,
   missing:updates.filter(x=>x[1]===null).length,
   strengthAvailable:updates.filter(x=>x[1]?.group!==null).length
 };
}
function sectorEvidenceMarkup(r,detail=false){
 const s=r?.v3_sector_research;
 const title=s?(s.name||'업종명 미확인')+' · DART '+s.prefix:'업종 미확인';
 const g=s?.group;
 const strength=v=>finite(v)?(v>0?'+':'')+v.toFixed(1)+'%p':'미확인';
 const sample=g?' · 관측 20/60일 '+g.sample20_60+'/'+g.listed+
   ' · 120일 '+g.sample120+'/'+g.listed:' · 동일일 업종 강도 미확인';
 return '<div class="u-sector-info" aria-label="업종 시장대비 상대강도 · 점수 미포함">'+
   '<span class="u-sector-label"><b>업종</b> '+esc(title)+'</span>'+
   '<span><b>시장대비 20일</b> '+strength(g?.m20)+'</span>'+
   '<span><b>60일</b> '+strength(g?.m60)+'</span>'+
   '<span><b>120일</b> '+strength(g?.m120)+'</span>'+
   (detail?'<small>현재 OpenDART 업종코드 2자리 그룹의 상대강도 중앙값'+sample+
     '. 같은 시장지수 대비 %p 차이. 과거 업종 구성·수정주가·지수 거래일 독립 검증 전이며 점수 배점 제외.</small>':'')+
   '</div>';
}
function industryEvidenceView(raw,asof){
 if(!raw||raw.schema!=='v3-observed-dart-current-sector-profit-sample-0.1'||
   raw.asof!==asof||!validDate(asof)||
   !Number.isInteger(raw.current_listed_total)||raw.current_listed_total<100||
   !Number.isInteger(raw.dart_current_industry_codes_observed)||
   !Number.isInteger(raw.mapped_industry_issuer_earnings_two_year_matched)||
   raw.mapped_industry_issuer_earnings_two_year_matched>
     raw.dart_current_industry_codes_observed||
   raw.full_market_industry_membership_pit_verified!==false||
   raw.sector_15_points_earned_issuer_count!==0||
   !raw.industry_subweights_locked||
   raw.industry_subweights_locked.early_rotation!==5||
   raw.industry_subweights_locked.profit_transition!==4||
   raw.industry_subweights_locked.improvement_diffusion!==3||
   raw.industry_subweights_locked.overheat_crowding_quality!==3||
   !Array.isArray(raw.groups)||raw.groups.length>300||
   !raw.source_sha256||Object.values(raw.source_sha256).some(v=>
     typeof v!=='string'||!/^[a-f0-9]{64}$/.test(v)))return null;
 const rows=[];
 for(const g of raw.groups){
   if(!g||!['KOSPI','KOSDAQ'].includes(g.market)||
     typeof g.dart_industry_code_prefix_2!=='string'||
     !/^[0-9]{2}$/.test(g.dart_industry_code_prefix_2)||
     !Number.isInteger(g.current_classified_issuers)||
     !Number.isInteger(g.issuer_year_pair_observed)||
     !(g.current_classified_issuers>=g.issuer_year_pair_observed&&
       g.issuer_year_pair_observed>=0)||
     g.unobserved_income_among_known_codes!==
       g.current_classified_issuers-g.issuer_year_pair_observed||
     g.group_is_approved_historical_industry_cohort!==false||
     g.group_15point_investment_score!==null||
     g.missing_marketwide_membership_cannot_be_rescaled!==true)return null;
   const observed=g.observed_earnings_improving_issuer_ratio_sample_only;
   // v0.1 stored samples may predate the new conservative-bounds fields.
   // Accept ONLY a complete older sample, never invent its missing bounds.
   const keys=['improvement_breadth_lower_bound_known_classified_only',
     'improvement_breadth_upper_bound_known_classified_only',
     'classified_sample_earnings_coverage_ratio'];
   const present=keys.map(k=>Object.prototype.hasOwnProperty.call(g,k));
   if(present.some(Boolean)&&!present.every(Boolean))return null;
   const low=present[0]?g[keys[0]]:null;
   const high=present[0]?g[keys[1]]:null;
   const coverage=present[0]?g[keys[2]]:null;
   const validRatio=v=>finite(v)&&v>=0&&v<=1;
   if(g.issuer_year_pair_observed<3){
     if(observed!==null||low!==null||high!==null||coverage!==null)return null;
   }else{
     if(!validRatio(observed))return null;
     if(present[0]&&(![low,high,coverage].every(validRatio)||
       low>observed+1e-5||high+1e-5<observed||low>high))return null;
   }
   rows.push({market:g.market,code:g.dart_industry_code_prefix_2,
     observed:g.issuer_year_pair_observed,classified:g.current_classified_issuers,
     coverage,improving:observed,lower:low,upper:high,
     growth:g.observed_pair_scope_uniform===true&&
       finite(g.sample_aggregate_operating_profit_growth_pct)?
       g.sample_aggregate_operating_profit_growth_pct:null});
 }
 if(rows.reduce((v,r)=>v+r.observed,0)!==
    raw.mapped_industry_issuer_earnings_two_year_matched ||
    rows.reduce((v,r)=>v+r.classified,0)!==
    raw.dart_current_industry_codes_observed)return null;
 rows.sort((a,b)=>b.observed-a.observed||a.market.localeCompare(b.market)||
   a.code.localeCompare(b.code));
 const format=v=>v===null?'—':(v*100).toFixed(1)+'%';
 const html='<p class="u-muted">공식 DART 기업개황 업종코드 '+
   raw.dart_current_industry_codes_observed+' / '+raw.current_listed_total+
   '개 · 2024~25년 영업이익 2개년 실제 대조 '+
   raw.mapped_industry_issuer_earnings_two_year_matched+
   '개. 아래 비율은 <strong>분류·실적 관측 표본만</strong>의 결과이며 산업 15점 및 종목 추천이 아닙니다.</p>'+
  '<div class="u-scroller"><table><thead><tr>'+
  '<th>시장 · 코드 앞 2자리</th><th>분류/실적</th><th>개선기업 (표본)</th>'+
  '<th>미확보 반영 범위*</th><th>표본 이익변화</th></tr></thead><tbody>'+
  rows.filter(r=>r.observed>=3).slice(0,24).map(r=>
    '<tr><th>'+esc(r.market)+' · '+esc(r.code)+'</th><td>'+
    r.observed+'/'+r.classified+'</td><td>'+format(r.improving)+
    '</td><td>'+(r.lower===null?'갱신 대기':format(r.lower)+'~'+format(r.upper))+
    '</td><td>'+
    (r.growth===null?'—':pct(r.growth))+'</td></tr>').join('')+
  '</tbody></table></div>'+
  '<p class="u-muted">* 미확보 반영 범위는 <strong>이미 분류된 기업 안에서만</strong> 가능한 비율의 상·하한입니다. 미분류 기업까지 고려한 전체 업종의 범위는 아닙니다. 표시 순서는 실적 확보 기업 수이며 매수 순위가 아닙니다. 서로 다른 연결·별도 재무제표가 섞이면 업종 이익변화는 비워둡니다.</p>';
 return {html,rows,coverage:raw.dart_current_industry_codes_observed,
         matched:raw.mapped_industry_issuer_earnings_two_year_matched};
}
// Nine-component receipt audit; these issuer counts are not earned scores.
function financialSubfactorCoverageView(raw,asof,listed){
 if(!raw||!validDate(asof)||raw.asof!==asof||
  !Number.isInteger(listed)||listed<100||raw.listed_total!==listed||
  raw.certified_100point_count!==0||
  raw.subfactor_missing_is_not_a_zero_score!==true)return '';
 const status=raw.financial_valuation_component_observation_status;
 const yes=raw.financial_valuation_component_observed_issuers;
 const no=raw.financial_valuation_component_unobserved_issuers;
 if(!status||!yes||!no||Array.isArray(yes)||Array.isArray(no)||
  typeof yes!=='object'||typeof no!=='object'||
  status.fiscal!=='same_day_replayed_component_receipts'||
  status.cashflow!=='same_day_receipt_matched_fcf')return '';
 const factors=[["revenue_cagr_3y_pct","재무","매출 3개년 연평균 성장률"],["operating_margin_3y_pct","재무","3개년 평균 영업이익률"],["liabilities_to_assets_pct","재무","부채 / 자산 비율"],["operating_roa_3y_pct","재무","3개년 영업자산수익률"],["financial_fcf_assets_3y","재무","3개년 FCF / 자산"],["pbr","가치","PBR (참고 계산치)"],["earnings_yield_pct","가치","이익수익률"],["sales_to_market_cap","가치","매출 / 시가총액"],["valuation_fcf_yield_latest","가치","최근 연도 FCF 수익률"]];
 const rows=[];
 for(const [key,area,title] of factors){
  if(!Object.prototype.hasOwnProperty.call(yes,key)||
   !Object.prototype.hasOwnProperty.call(no,key))return '';
  const observed=yes[key],missing=no[key];
  if(observed===null&&missing===null){
   rows.push('<tr><th scope="row">'+esc(title)+'</th><td>'+area+
    '</td><td>미확인</td><td>미확인</td><td>—</td></tr>');
   continue;
  }
  if(!Number.isInteger(observed)||observed<0||observed>listed||
   !Number.isInteger(missing)||missing<0||missing>listed||
   observed+missing!==listed)return '';
  rows.push('<tr><th scope="row">'+esc(title)+'</th><td>'+area+
   '</td><td>'+observed.toLocaleString('ko-KR')+'개</td><td>'+
   missing.toLocaleString('ko-KR')+'개</td><td>'+
   (100*observed/listed).toFixed(1)+'%</td></tr>');
 }
 return '<details class="u-subfactor-breakdown">'+
  '<summary>재무·가치평가 9개 세부지표 원천 확보 현황</summary>'+
  '<div class="u-coverage-scroll"><table class="u-coverage-table">'+
  '<thead><tr><th scope="col">세부 지표</th><th scope="col">영역</th>'+
  '<th scope="col">확보 기업</th><th scope="col">미확보 기업</th>'+
  '<th scope="col">확보율</th></tr></thead><tbody>'+
  rows.join('')+'</tbody></table></div>'+
  '<p class="u-source">같은 기준일에 원천자료가 일치하는 기업 수입니다. '+
  '미확보는 0점이 아닌 미확인입니다. 3개년 FCF는 필요한 연도별 근거를 모두 확보한 경우에만 집계합니다. '+
  'PBR은 참고 계산치이며 거래소 공식 지표로 검증된 수치가 아닙니다. '+
  '이 표는 투자점수나 종목 추천 순위가 아닙니다.</p></details>';
}

// Distinguish the source ingestion ceiling from the actual per-issuer usable
// research coverage after independently dated factors have been retained.
function retainedCoverageSummary(model,asof,listed){
 if(!model||model.date!==asof||!Array.isArray(model.rows)||
    model.rows.length!==listed)return null;
 let peak=0,carriedIssuers=0;
 for(const x of model.rows){
  if(!x?.r||x.r.price_date!==asof)return null;
  const c=researchComposite(x.r);
  if(!Number.isInteger(c.available)||c.available<0||c.available>100)return null;
  peak=Math.max(peak,c.available);
  if(c.carriedPillars>0)carriedIssuers++;
 }
 return {peak,carriedIssuers};
}
// A five-pillar data availability audit; counts are evidence, never earned points.
function fiveAreaCoverageView(raw,asof,listed,model=null){
 if(!raw||raw.schema!=='v3-current-day-all-listed-observed-plus-dart-fcf-0.3'||
  !validDate(asof)||raw.asof!==asof||
  !Number.isInteger(listed)||listed<100||raw.listed_total!==listed||
  raw.certified_100point_count!==0||raw.missing_weight_is_not_zero!==true||
  raw.not_an_investment_ranking!==true||!raw.area_budgets||
  !raw.area_available_weight_distribution||!raw.observed_issuer_counts||
  !raw.complete_area_issuer_counts||!raw.factor_observed_weight_ceiling||
  !raw.factor_unobserved_weight_gap||!raw.unacquired_weight_issuer_slots||
  Object.keys(raw.area_budgets).length!==PILLARS.length)return null;
 if(raw.observed_issuer_counts.industry!==undefined&&
    raw.observed_issuer_counts.industry!==0)return null;
 let weightCeiling=0,missingWeight=0,missingIssuerWeights=0;
 const lines=[];
 for(const [area,title] of PILLARS){
  const budget=WEIGHTS[area],dist=raw.area_available_weight_distribution[area];
  const count=raw.observed_issuer_counts[area];
  if(raw.area_budgets[area]!==budget||
    !dist||Array.isArray(dist)||typeof dist!=='object'||
    !Number.isInteger(count)||count<0||count>listed)return null;
  let total=0,observed=0,ceiling=0,missingSlots=0;
  for(const [weight,value] of Object.entries(dist)){
   if(!/^(0|[1-9][0-9]*)$/.test(weight)||
     Number(weight)>budget||
     !Number.isInteger(value)||value<0||value>listed)return null;
   total+=value;
   if(Number(weight)>0)observed+=value;
   if(value>0)ceiling=Math.max(ceiling,Number(weight));
   missingSlots+=(budget-Number(weight))*value;
  }
  const complete=dist[String(budget)]||0;
  if(total!==listed||observed!==count||
    raw.complete_area_issuer_counts[area]!==complete||
    raw.factor_observed_weight_ceiling[area]!==ceiling||
    raw.factor_unobserved_weight_gap[area]!==budget-ceiling||
    raw.unacquired_weight_issuer_slots[area]!==missingSlots)return null;
  weightCeiling+=ceiling;missingWeight+=budget-ceiling;
  missingIssuerWeights+=missingSlots;
  lines.push('<tr><th scope="row">'+esc(title)+'</th>'+
    '<td>'+count.toLocaleString('ko-KR')+' / '+listed.toLocaleString('ko-KR')+
    ' <small>('+((count/listed)*100).toFixed(1)+'%)</small></td>'+
    '<td>'+complete.toLocaleString('ko-KR')+'</td>'+
    '<td>'+ceiling+' / '+budget+'점</td>'+
    '<td>'+(budget-ceiling)+'점</td></tr>');
 }
 if(weightCeiling!==raw.highest_currently_observable_weight||
   weightCeiling+missingWeight!==100||
   raw.total_unobserved_weight_gap!==missingWeight||
   raw.total_unacquired_weight_issuer_slots!==missingIssuerWeights)return null;
 // This is a genuine SAME-ISSUER overlap, distinct from combining the
 // maximum weight of different issuers into an apparent 100 point ceiling.
 const complete100=raw.provisional_complete_100point_observation_count;
 const minComplete=Math.min(...PILLARS.map(([key])=>raw.complete_area_issuer_counts[key]));
 if(complete100!==undefined&&
    (!Number.isInteger(complete100)||complete100<0||
     complete100>minComplete||complete100>listed))return null;
 const completeNote=complete100===undefined
  ?'<p class="u-source">121거래일 신규 관측값의 전영역 100점 배점 확보 건수는 다음 수집 이후 표시됩니다.</p>'
  :'<p class="u-source">전영역 100점 배점 관측 완료: <b>'+
   complete100.toLocaleString('ko-KR')+'개</b> (정식 투자점수 인증: 0개). '+
   '121거래일 경로 기반 추가 11점은 수정주가·기업행위에 대한 독립 거래소 검증 전인 잠정 연구점수입니다.</p>';
 const continuity=retainedCoverageSummary(model,asof,listed);
 const continuityMarkup=continuity?
  '<p class="u-source"><b>종목별 최근 유효 관측 기준 확보배점 최고 '+
  continuity.peak+'/100점</b> · 이전 관측값으로 점수를 유지한 종목 '+
  continuity.carriedIssuers.toLocaleString('ko-KR')+'개. '+
  '이 값은 아래 당일 원천 수집현황과 다르며 스크리너 표시·정렬에 사용합니다. '+
  '각 영역의 관측 기준일을 확인하세요.</p>':'';
 return '<div class="u-coverage-audit"><div class="u-coverage-top">'+
  '<strong>5개 영역 실제 데이터 확보</strong>'+
  '<span>당일 원천 영역별 최대 배점 합산 <b>'+weightCeiling+'/100점</b> · 미확보 <b>'+
  missingWeight+'점</b></span></div>'+
  continuityMarkup+
  '<div class="u-coverage-scroll"><table class="u-coverage-table"><thead><tr>'+
  '<th scope="col">영역</th><th scope="col">관측 기업 / 상장 전체</th>'+
  '<th scope="col">영역 전체 확보</th><th scope="col">관측 가능 배점</th>'+
  '<th scope="col">미확보 배점</th>'+
  '</tr></thead><tbody>'+lines.join('')+'</tbody></table></div>'+
  '<p class="u-source">미확보 배점은 각 영역에서 아직 단 한 종목에도 확인되지 않은 관측 한도입니다. '+
  '개별 종목별 미확보량은 더 클 수 있으며, 관측 가능 배점은 검증된 투자점수나 실제 획득점수가 아닙니다. '+
  '미확보 항목은 0점 처리하지 않고 미확인으로 유지합니다. 산업은 배점에서 제외됩니다.</p>'+
  completeNote+financialSubfactorCoverageView(raw,asof,listed)+'</div>';
}
const api={VERSION,WEIGHTS,PILLARS,VOLUME_COMPONENTS,stabilityProfile,compareStableResearchScores,volumeStatus,volumeParts,observedVolumeEvidence,obvLagCandidate,applyObservedVolume,applyCurrentPriceReplay,esc,won,pct,eok,validDate,validPattern,evidence,
 technicalEvidence,momentumEvidence,technicalCandidate,pillarState,researchComposite,personalHundredFromComposite,personalHundred,scoreOverviewMarkup,scorePillarExplanation,issuerReadinessMarkup,subfactorHistoryMarkup,lastObservedFactorChip,comparisonPillarBars,fcfEvidenceStatus,fcfEvidenceMarkup,issuerCashflowGapMarkup,applyIssuerCashflowEvidence,matchCompareStock,resolveComparePair,coverageVector,coverageKey,coverageGroups,KSIC2_NAMES,sectorEvidenceMarkup,applyIndustryReference,riskState,rankItems,compareResearchScores,
 sampleQuant,quantSourceQualityView,summary,marketFreshness,weeklyCoverageText,realSourceCoverageText,actualCashflowCoverageText,industryEvidenceView,industryPriceEvidenceView,fiveAreaCoverageView,retainedCoverageSummary,financialSubfactorCoverageView,applyRotationTechnicalBackfill,applyBulkFinancialPreview,applyActualCashflowPreview,applyCurrentObservedFinance,applyIssuerFactorLedger};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
root.V3UnifiedCore=api;
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
if(!$('unifiedApp'))return;
let weeklyCoverage=null,realSavedCoverage=null,actualCashflowCoverage=null;
let model=null,core=null,tab='discover',sort='stable',safeOnly=false,top100Only=false,obvOnly=false,selected=null,limit=20,compareShown=false;
function refreshCoverageGroups(data){
 const control=$('coverageGroup');
 const previous=control.value;
 control.replaceChildren();
 const all=D.createElement('option');
 all.value='all';all.textContent='전체 배점 그룹';
 control.appendChild(all);
 for(const group of coverageGroups(data)){
  const opt=D.createElement('option');
  opt.value=group.key;
  opt.textContent=group.total+'/100 · 거'+group.parts[0]+'/기'+group.parts[1]+
   '/재'+group.parts[2]+'/가'+group.parts[3]+'/모'+group.parts[4]+
   ' · '+group.count.toLocaleString('ko-KR')+'종목';
  opt.title='거래량/기술/재무/가치/모멘텀 산출 가능 배점 · 정식 비교 인증 아님';
  control.appendChild(opt);
 }
 if([...control.options].some(opt=>opt.value===previous))control.value=previous;
}
function label(item){return item.stage==='형상 후보'?'형상 후보':item.stage;}
function patternPill(item){
 const weekly=item.timeframe==='weekly';
 return '<span class="u-signal '+(weekly?'week':'day')+'">'+
  (weekly?'주봉 ':'일봉 ')+esc(item.label)+' · '+esc(label(item))+'</span>';
}

function renderCompare(){
 const results=$('compareResults'),status=$('compareStatus');
 if(!compareShown||!model){
  results.hidden=true;results.innerHTML='';return;
 }
 const pair=resolveComparePair(model,$('compareCodeA').value,$('compareCodeB').value);
 if(!pair.ok){
  const messages={
   left_required:'첫 번째 종목의 이름이나 6자리 코드를 입력해 주세요.',
   right_required:'두 번째 종목의 이름이나 6자리 코드를 입력해 주세요.',
   left_not_found:'첫 번째 종목을 찾지 못했습니다. 정확한 종목명 또는 6자리 코드를 확인하세요.',
   right_not_found:'두 번째 종목을 찾지 못했습니다. 정확한 종목명 또는 6자리 코드를 확인하세요.',
   same_stock:'동일한 종목은 비교할 수 없습니다. 다른 종목을 선택하세요.'
  };
  status.textContent=messages[pair.reason]||'종목을 확인할 수 없습니다.';
  results.hidden=true;results.innerHTML='';
  return;
 }
 const {left,right,a,b}=pair;
 const compareNum=c=>{
  const value=personalHundredFromComposite(c);
  return value===null?'관측 부족':value.toFixed(1)+' / 100';
 };
 const names=[left.r.name,right.r.name];
 const cards=[{x:left,c:a},{x:right,c:b}].map(({x,c},i)=>{
  const risk=riskState(x,core);
  return '<article class="u-compare-card"><h3>'+esc(names[i])+
   ' <small>'+esc(x.r.code)+' · '+esc(x.r.market)+'</small></h3>'+
   '<div class="u-compare-kpis"><div><small>종가 · '+esc(x.r.price_date)+'</small><b>'+won(x.r.price)+'</b></div>'+
   '<div><small>개인용 100점 환산 · 미인증</small><b>'+compareNum(c)+'</b></div>'+
   '<div><small>V3 잠정 합계 · 미검증</small><b>'+
   (c.earned===null?'미산출':c.earned.toFixed(1)+'점')+'</b></div>'+
   '<div><small>확보 배점</small><b>'+c.available+'/100</b></div></div>'+
   '<p class="u-compare-risk">손절 참고: '+
   (risk.distance===null?'미확인':pct(-risk.distance))+' · '+esc(risk.description)+'</p></article>';
 }).join('');
 const details=a.pillars.map((p,i)=>{
  const q=b.pillars[i];
  const points=v=>v.candidate===null?'미산출 · 0점 아님':v.candidate.toFixed(1)+'점';
  return '<tr><th scope="row">'+esc(p.label)+' /'+p.max+'</th>'+
   '<td>'+points(p)+'<small>확보 '+p.available+'/'+p.max+
   (p.asof?' · '+esc(p.asof):'')+'</small></td>'+
   '<td>'+points(q)+'<small>확보 '+q.available+'/'+q.max+
   (q.asof?' · '+esc(q.asof):'')+'</small></td></tr>';
 }).join('');
 const comparable=pair.sameDate&&pair.sameCoverage&&
  a.pillars.every((p,i)=>p.asof===b.pillars[i].asof);
 status.textContent=comparable?
  '두 종목의 관측 기준일과 5영역 확보 배점 구성이 같습니다. 투자성과나 원천 검증을 뜻하지 않습니다.':
  '5개 영역의 관측 기준일 또는 확보 배점 구성이 달라 점수 우열을 직접 판단할 수 없습니다.';
 results.innerHTML='<div class="u-compare-cards">'+cards+'</div>'+
  comparisonPillarBars(a,b,names,comparable)+
  '<details class="u-compare-table-details"><summary>영역별 정확한 점수·배점 수치표 펼치기</summary>'+
  '<div class="u-compare-overflow"><table class="u-compare-table">'+
  '<caption>5개 영역별 실제 잠정 점수와 확보 배점 비교</caption>'+
  '<thead><tr><th scope="col">영역</th><th scope="col">'+esc(names[0])+
  '</th><th scope="col">'+esc(names[1])+'</th></tr></thead>'+
  '<tbody>'+details+'</tbody></table></div></details>'+
  '<p class="u-compare-disclaimer">개인 연구용 비교 · 관측하지 못한 영역은 0점이 아닙니다. '+
  '공시 효력시점·기업행위 수정주가·기관 수급·실제 수익성과 위험은 인증되지 않았습니다.</p>';
 results.hidden=false;
 paintScoreOverview(results);
}
function addToCompare(code){
 if(!model)return;
 const a=$('compareCodeA'),b=$('compareCodeB');
 const one=matchCompareStock(model,a.value)?.r.code;
 const two=matchCompareStock(model,b.value)?.r.code;
 if(one===code||two===code){
  $('compareStatus').textContent='이미 비교 대상으로 선택한 종목입니다.';
  return;
 }
 if(!a.value.trim())a.value=code;
 else if(!b.value.trim())b.value=code;
 else {a.value=b.value;b.value=code;}
 compareShown=!!(a.value.trim()&&b.value.trim());
 if(compareShown)renderCompare();
 else $('compareStatus').textContent='첫 번째 종목을 담았습니다. 다른 종목을 하나 더 선택하세요.';
 $('stockCompare').open=true;
 $('stockCompare').scrollIntoView({behavior:'smooth',block:'start'});
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
 const stability=x.stability||stabilityProfile(r,researchComposite(r));
 const quant=sampleQuant(x,core);
 const composite=researchComposite(r);
 const personal100=personalHundredFromComposite(composite);
 view.hidden=false;
 view.innerHTML='<div class="u-detail-top"><div><small>종목별 분석 · '+esc(model.date)+'</small>'+
 '<h2>'+esc(r.name)+'</h2><p>'+esc(r.code)+' · '+esc(r.market)+'</p></div>'+
 '<button id="detailClose" type="button" aria-label="분석 닫기">✕</button></div>'+
 '<div class="u-verdict '+(risk.unsafe?'warning':'neutral')+'"><strong>'+esc(risk.description)+'</strong>'+
 '<span>V3 잠정점수는 미검증 연구값이며 매수 신호가 아닙니다.</span></div>'+
 scoreOverviewMarkup(composite,personal100,r)+issuerReadinessMarkup(r)+
 (root.V3StopEvidence?root.V3StopEvidence.markup(r,model?.date):'')+
 '<div class="u-stability-note"><strong>안정형 보정 '+(stability.score===null?'미산출':stability.score.toFixed(1)+'/100')+'</strong> · V3 관측점수에서 시총 규모 -'+stability.capitalDeduction+'점 · 구조 손절거리 '+(stability.stopAdjustment>=0?'+':'')+stability.stopAdjustment+'점 · '+esc(stability.status)+'<small>시총 '+(stability.capitalEok===null?'미확인':eok(stability.capitalEok*1e8))+' · 당일 거래대금 '+(stability.tradingEok===null?'미확인':eok(stability.tradingEok*1e8))+'(정보만 표시·0점 영향) · 손절거리 '+(stability.stopDistancePct===null?'미확인':stability.stopDistancePct.toFixed(1)+'%')+' · 자료일 '+esc(stability.asof||'미확인')+' · 기간별 실제 거래대금 20·60·120·240일/5·10·20·60·120주 검증 전 미반영</small></div>'+
 '<div class="u-detail-grid"><div><small>종가</small><strong>'+won(r.price)+'</strong></div>'+
 '<div><small>구조 무효화 참고선</small><strong>'+won(r.stop_line)+'</strong></div>'+
 '<div><small>참고 손절거리</small><strong>'+(risk.distance===null?'미확인':pct(-risk.distance))+'</strong></div>'+
 '<div class="u-factor-unverified"><small>V3 잠정 종합점수 (미검증)</small><strong>'+
 (composite.earned===null?'—':composite.earned.toFixed(1)+' / 100')+
 '</strong><small>산출 가능 배점 '+composite.available+'/100 · 표시일 '+esc(composite.asof)+
 (composite.stalePillars?' · 이전 관측 '+composite.stalePillars+'영역 포함':'')+
 '</small></div>'+
 '<div class="u-personal100"><small>개인 연구용 100점 환산 · 미인증</small><strong>'+
 (personal100===null?'— · 관측 부족':personal100.toFixed(1)+' / 100')+
 '</strong><small>관측 잠정점수 ÷ 확보 배점 × 100 · 5영역 모두 관측·75/100 이상만</small></div></div>'+
 '<h3>잠정 합계 산출근거</h3>'+
 '<p class="u-muted">아래 연구점수 중 실제로 계산된 값만 더한 합계입니다. '+
 '미확보 영역을 0점으로 처리하지 않습니다. 별도 개인용 100점 환산은 관측된 점수의 비율만 표시하며 결측영역의 실제 점수나 미래 수익을 추정하지 않습니다. '+
 '각 영역은 최근 유효 관측일의 값을 유지하며, 일부는 오늘 계산한 값이 아닙니다. 매수 추천이 아닙니다.</p>'+
 '<h3>일봉 · 스윙 관찰</h3>'+
 (x.daily.length?'<div class="u-signals">'+x.daily.map(patternPill).join('')+'</div>':
 '<p class="u-muted">확인된 선호 패턴·RSI 관측 없음 또는 당일 자료 미확보</p>')+
 '<h3>주봉 · 중기/장기 관찰</h3>'+
 (x.weekly.length?'<div class="u-signals">'+x.weekly.map(patternPill).join('')+'</div>':
 '<p class="u-muted">완성 주봉의 패턴·RSI 관측 미확인 (진행 중인 주봉 제외)</p>')+
 '<h3>소속 업종 · 시장 대비 20·60·120일 상대강도</h3>'+
 sectorEvidenceMarkup(r,true)+
 '<p class="u-muted">산업·업종은 5개 점수 영역의 배점에 포함하지 않습니다. DART 업종코드 기준 그룹 표본값이며 실제 KRX 업종지수 수익률로 오해하지 마세요.</p>'+
 '<details class="u-detail-evidence"><summary>점수 세부 근거 · 재무 · 퀀트 펼쳐보기</summary>'+
 '<h3>거래량·OBV /20 · 실제 자료 관측</h3>'+
 '<p class="u-muted">'+esc(volumeStatus(pillars.find(p=>p.key==='volume')))+'</p>'+
 volumeParts(r)+
 '<p class="u-muted">저장된 20일 거래량 비율·60일 OBV 방향·주가 미반영 16점까지만 산출합니다. 4점 매물 소화는 미확보이며 기존 기술25의 당일 거래량 점수와 중복하지 않습니다. 정규장 원자료·기업행사는 미검증입니다.</p>'+
 '<p class="u-muted">기술적 분석 25점 안의 근거별 구성 (일봉/주봉 독립 평가)</p>'+
 technicalParts(r)+
 '<h3>재무 · 밸류 참고</h3>'+
 '<p class="u-muted"><b>현재 가치 20점 배점:</b> PBR 5 · 이익수익률 5(지배주주순이익/시총; PER 역수 유사, 적정 PER 검증 아님) · 매출/시총 5 · FCF수익률 5. <b>EPS 성장성·지속성은 아직 미수집·미평가</b>입니다. 공시의 실제 주당순이익과 주식분할·정정·공시 효력일 검증 후 배점을 개편할 예정이며, 결측을 0점 처리하거나 기존 점수를 지우지 않습니다.</p>'+
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
  ' · 공시 정정·시점(PIT) 검증 전으로 재무 20점과 가치 20점은 정식 투자점수 미확정입니다. 일부 확인된 근거만 위 잠정점수에 반영합니다.</p>'+
 '<p class="u-muted">실제 DART FCF 연구값 (미검증): 3년 FCF/자산 '+(finite(r.v3_dart_fcf_research?.raw_observed_financial_fcf_assets_3y_pct)?
  pct(r.v3_dart_fcf_research.raw_observed_financial_fcf_assets_3y_pct):'미확보')+
  ' · FCF수익률 '+(finite(r.v3_dart_fcf_research?.raw_observed_valuation_fcf_yield_pct)?
  pct(r.v3_dart_fcf_research.raw_observed_valuation_fcf_yield_pct):'미확보')+
  ' · 잠정점수는 동시장 표본 30개 이상 충족 시에만 반영, 확정 매수점수 아님.</p>'+
 fcfEvidenceMarkup(r)+
 '<p class="u-muted">비공식 간이 P/B '+(book!==null?book.toFixed(2)+'배':'미산출')+
 ' · 현행 가치20점 내 PBR5점의 참고 근거일 수 있으며, 공식 거래소 PBR 검증은 별도 필요.</p>'+
 '<h3>퀀트 · 과거 유사조건</h3><p class="u-muted">'+esc(quant)+'</p>'+
 quantTable(x,core)+
 '</details>'+ 
 '<h3>손실 위험 · 가설 무효화</h3><p class="u-muted">연구점수와 별도로 위험요인을 점검해야 합니다'+
 ' · '+esc(risk.reasons.join(' / ')||'추가 위험 경고 관측 없음')+
 '. 구조선 이탈 시 재검토. 실제 체결 손실은 더 클 수 있습니다.</p>'+
 '<div class="u-actions"><button type="button" id="addToCompare" data-code="'+esc(r.code)+'">이 종목 비교에 담기</button><a href="'+esc(stockLink)+'" target="_blank" rel="noopener noreferrer">종목 차트 ↗</a>'+
 '<button type="button" id="backToList">목록으로</button></div>';
 paintScoreOverview(view);
 $('detailClose').onclick=()=>{view.hidden=true;selected=null;draw();};
 $('addToCompare').onclick=()=>addToCompare(r.code);
 $('backToList').onclick=()=>{$('stockList').scrollIntoView({behavior:'smooth',block:'start'});};
 if(root.matchMedia?.('(max-width: 860px)')?.matches)view.scrollIntoView({behavior:'smooth',block:'start'});
}
function draw(){
 if(!model)return;
 const query=$('stockSearch').value.trim();
 const coverageOnly=$('coverageGroup').value;
 const rows=rankItems(model,core,tab,$('market').value,query,sort,safeOnly,top100Only,obvOnly,coverageOnly);
 $('filterNotice').textContent=query?
   '전체 시장 검색 결과 · TOP100·OBV·배점 그룹·선택 탭·위험 필터 제한 없음':
   (coverageOnly!=='all'?'5영역별 산출 가능 배점 구성이 같은 종목만 표시합니다. 세부원천·PIT·수익 검증을 보증하지 않습니다.'+(obvOnly?' OBV 연구 필터도 적용 중입니다.':''):
   obvOnly?'60일 OBV 상승·주가 미반영 실측 연구 후보만 표시합니다. 정식 매집·매수 신호는 아닙니다.':
   '종목명 검색은 TOP100 제한 없이 전체에서 검색됩니다.');
 $('resultCount').textContent=rows.length.toLocaleString('ko-KR')+
  '종목 · '+(query?'전체 시장 검색 · ':top100Only?'현재 정렬 상위 100개 중 ':'전체 시장 중 ')+
  (obvOnly&&!query?'OBV 상승·주가 미반영 연구 관찰 · ':'')+
  (coverageOnly!=='all'&&!query?'5영역별 확보배점 '+coverageOnly.split('-').join('/')+' 동일 그룹 · ':'')+
   (sort==='stable'?'안정형 보정순 (시총 소폭 보정·손절 구조 가감점)':sort==='score'?'V3 잠정점수 관측범위 우선':sort==='personal'?'개인 연구용 100점 환산순':sort==='risk'?'손절 위험순':'패턴 관찰순')+
  ' · 점수 미검증 · 관측범위가 다른 종목 간 직접 순위 비교 주의';
 $('stockList').innerHTML=rows.length?rows.slice(0,limit).map((x,i)=>{
  const r=x.r;
  const tags=(obvLagCandidate(r)?'<span class="u-signal u-obv-lag">OBV↑ · 주가 미반영 관찰</span>':'')+
   x.signals.slice(0,3).map(patternPill).join('');
  const stable=x.stability;
  const state=x.risk.unsafe?'위험 주의':x.risk.distance===null?'손절선 미확인':'구조적 위험 검토';
  return '<button class="u-stock '+(selected===r.code?'selected':'')+
   '" type="button" data-stock="'+esc(r.code)+'" aria-label="'+esc(r.name)+' 종목 상세 분석 열기">'+
   '<div class="u-stock-name"><span class="u-ranked"><b class="u-rank-number" title="현재 조건에서의 표시 순서">'+
   String(i+1).padStart(2,'0')+'</b><strong>'+esc(r.name)+'</strong></span><small>'+
   esc(r.code)+' · '+esc(r.market)+'</small></div>'+
   '<div class="u-card-kpis"><div class="u-card-price"><small>종가 · '+esc(r.price_date||model.date)+'</small><b>'+won(r.price)+'</b></div>'+
   '<div class="u-card-score"><small>'+(sort==='stable'?'안정형 보정 · 잠정':'V3 잠정점수 · 미검증')+'</small><strong>'+
   (sort==='stable'?(stable.score===null?'—':stable.score.toFixed(1)):
    (x.composite.earned===null?'—':x.composite.earned.toFixed(1)))+'<small> / 100</small></strong>'+
   (sort==='stable'?'<small>V3 원점수 '+(x.composite.earned===null?'—':x.composite.earned.toFixed(1))+' · 보정 '+(stable.deduction>0?'-':stable.deduction<0?'+':'')+Math.abs(stable.deduction)+'</small>':
    sort==='personal'?'<small>개인환산 '+(x.personal100===null?'—':x.personal100.toFixed(1))+'점</small>':'')+'</div>'+
   '<div class="u-card-coverage"><small>산출 배점</small><b>'+x.composite.available+'/100</b></div></div>'+
   '<div class="u-compact-pillars" aria-label="5개 영역별 잠정점수 · 자세한 기준일과 검증 상태는 종목 상세">'+
   x.composite.pillars.map(p=>{
    const short={volume:'거래량',technical:'기술',financial:'재무',valuation:'가치',momentum:'모멘텀'}[p.key]||p.label;
    return '<span data-pillar="'+esc(p.key)+'" title="'+esc(p.label+' · 확보 '+p.available+'/'+p.max+
      ' · 기준일 '+(p.asof||'미확인')+' · 미검증')+'"><small>'+esc(short)+'</small><b>'+
      (p.candidate===null?'—':p.candidate.toFixed(1))+'</b></span>';
   }).join('')+'</div>'+
   '<div class="u-card-meta"><span>시총 '+(stable.capitalEok===null?'미확인':Math.round(stable.capitalEok).toLocaleString('ko-KR')+'억')+'</span>'+
   '<span>당일 거래대금 '+(stable.tradingEok===null?'미확인':stable.tradingEok.toLocaleString('ko-KR',{maximumFractionDigits:1})+'억')+'</span>'+
   '<span>업종 '+esc(r.v3_sector_research?.name||'미확인')+'</span>'+
   '<span>점수 관측일 '+esc(x.composite.asof||model.date)+
   (x.composite.stalePillars?' · 이전 관측 '+x.composite.stalePillars+'영역':'')+
   '</span></div>'+
   '<div class="u-tags">'+(tags||'<span class="u-faint">확인된 차트 신호 없음</span>')+'</div>'+
   '<div class="u-stock-foot"><span class="'+(x.risk.unsafe?'u-danger':'u-caution')+'">'+esc(state)+'</span>'+
   '<span>손절 참고 '+(x.risk.distance===null?'미확인':x.risk.distance.toFixed(1)+'%')+'</span>'+
   '<span class="u-open-hint">상세 보기 →</span></div></button>';
 }).join(''):'<div class="u-empty">해당 조건을 만족하는 종목이 없습니다. 후보를 임의로 만들지 않습니다. 필터를 변경해 확인할 수 있어요.</div>';
 $('more').hidden=rows.length<=limit;
 $('top100Only').setAttribute('aria-pressed',String(top100Only));
  $('obvLagOnly').setAttribute('aria-pressed',String(obvOnly));
 $('personalRank').setAttribute('aria-pressed',String(sort==='personal'));
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
  '종목. 시총(연속형 최대 -5점)과 구조 손절거리(-3~+2점)만 안정형 보정에 반영합니다. 당일 거래대금은 참고값으로만 표시하며 점수에 반영하지 않습니다. 과거 실제 거래대금 20·60·120·240일 및 5·10·20·60·120주 비교 자료는 미확보 상태이고, 기존 5개 영역 점수와 거래량·OBV 산식은 아직 변경하지 않았습니다. 거래량·OBV 신규 관측은 '+data.rows.filter(x=>
    observedVolumeEvidence(x.r)!==null).length+
  '종목 / 관측가능 16점·미확보 4점입니다. 실시간 투자등급이 아닌 미검증 점수입니다. '+
  '일부 데이터가 누락되어 있으면 합계가 낮게 표시될 수 있습니다. '+
  '당일 CSV 재무 잠정점수 표시 '+data.rows.filter(x=>
    x.r.v3_same_day_observed_finance?.financial?.available>0).length+
  '종목(동일 기준일 확인) · 4개년 재무 참고 '+data.rows.filter(x=>x.fourYearRef!==null).length+
  '종목 (재무 20점 확정 아님). 업종은 100점 배점에서 제외하고, 종목별 현재 DART 업종과 20·60·120일 시장대비 상대강도를 점수 외 참고자료로 표시합니다. 확인되지 않은 수치는 미확인으로 표시합니다.'+
  weeklyCoverageText(weeklyCoverage,model.date)+
  realSourceCoverageText(realSavedCoverage,model.date)+
  actualCashflowCoverageText(actualCashflowCoverage,model.date,model.marketEligible);
 refreshCoverageGroups(data);
 if(compareShown)renderCompare();
 draw();
}
async function load(){
 const s=$('state');s.className='u-state';s.textContent='장마감 분석 자료 확인 중…';
 const files=['data.json','quant.json','recommendations.json','official.json',
  'v3-priority-quant.json','v3-dart-book-reference.json','v3-weekly-coverage.json',
  'v3-weekly-rotation.json','v3-dart-four-year-reference.json',
  'v3-dart-bulk-provisional.json','v3-real-source-six-area-coverage.json',
  'v3-dart-fcf-research-preview.json',
  'v3-dart-industry-earnings-sample.json','v3-volume-obv-observed.json',
  'v3-current-price-technical-momentum.json',
  'v3-industry-observed-relative-strength.json',
  'v3-current-observed-all-listed-summary.json',
  'v3-dart-industry-classification.json',
  'v3-dart-cashflow-issuer-evidence.json',
  'v3-issuer-last-observed-factors.json'];
 try{
  const [responses,observedCsv]=await Promise.all([
   Promise.all(files.map(async file=>{
   try{const r=await root.fetch(file+'?v='+Date.now(),{cache:'no-store'});
    return r.ok?await r.json():null;}catch(_){return null;}
   })),
   root.fetch('v3-current-observed-all-listed.csv?v='+Date.now(),
              {cache:'no-store'}).then(r=>r.ok?r.text():null).catch(()=>null)
  ]);
  core=root.V3RankedCore;
  if(!core)throw Error('missing_research_core');
  model=core.prepare(...responses.slice(0,6),responses[8]);
  const quantQuality=quantSourceQualityView(responses[1],model.date);
  if($('quantQualityStatus'))$('quantQualityStatus').textContent=
   quantQuality?.summary||'9년 퀀트 가격 이력 확보율 · 검증 자료 대기';
  if($('quantQualityNotes'))$('quantQualityNotes').textContent=
   quantQuality?.text||
   '동일 기준일의 원천 가격 품질 집계가 없거나 전체 종목 수·제외 원인 검증이 불완전합니다. 이전 연구 수치를 현재 확보율로 사용하지 않습니다.';
  if($('quantQualityQueue')){
   $('quantQualityQueue').innerHTML=quantQuality?.queueHTML||'';
   $('quantQualityQueue').hidden=!quantQuality?.queueHTML;
  }
  weeklyCoverage=responses[6];
  applyRotationTechnicalBackfill(model,responses[7]);
  applyBulkFinancialPreview(model,responses[9]);
  realSavedCoverage=responses[10];
  actualCashflowCoverage=responses[11];
  const attachedFcf=applyActualCashflowPreview(model,actualCashflowCoverage);
  applyIssuerCashflowEvidence(model,responses[18],
   attachedFcf>0?actualCashflowCoverage:null);
  applyObservedVolume(model,responses[13]);
  applyCurrentPriceReplay(model,responses[14]);
  // All 2,497 listed rows, same market day, independently replayed CSV.
  // Reject the entire overlay if any header, coverage, identity or subtotal
  // differs. Never double-add its FCF components to the already summed area.
  applyCurrentObservedFinance(model,responses[16],observedCsv);
  applyIssuerFactorLedger(model,responses[19]);
  if($('fiveAreaCoverage'))$('fiveAreaCoverage').innerHTML=
   fiveAreaCoverageView(responses[16],model.date,model.marketEligible,model)||
   '<p class="u-muted">영역별 실제 확보 현황을 확인할 수 없습니다. 자료 검증 후 표시합니다.</p>';
  const industry=industryEvidenceView(responses[12],model.date);
  const industryPrice=industryPriceEvidenceView(responses[15],model.date,model.marketEligible);
  applyIndustryReference(model,responses[17],industryPrice);
  $('industryResearchContent').innerHTML=
    (industryPrice?industryPrice.html:'<p class="u-muted">동일 시장일 업종 상대강도 집계 자료 동기화 대기 중입니다.</p>')+
    (industry?industry.html:
    '<p class="u-muted">업종별 실제 실적 표본은 원천 날짜 또는 진위 검증 대기 중입니다. 산업점수를 임의로 표시하지 않습니다.</p>');
  $('industryResearchCount').textContent=industryPrice?
    ('가격 관측 '+industryPrice.paired+'개 · 회복 관찰 '+industryPrice.watch+'개 그룹'):
    (industry?'분류 '+industry.coverage+'개 · 실적 대조 '+industry.matched+'개':
    '자료 검증 대기');
  selected=null;$('detail').hidden=true;
  present(model);
 }catch(err){
  // Log only the exception class and message for browser CI diagnostics;
  // never log source price rows or provider credentials.
  if(root.console&&typeof root.console.error==='function')
   root.console.error('[V3 workspace load] '+String(err?.name||'Error')+': '+String(err?.message||'unknown'));
  model=null;s.className='u-state error';
  s.textContent='현재 전 종목 관측자료를 검증할 수 없습니다. 잘못된 추천 대신 데이터를 보류합니다.';
  $('stockList').innerHTML='<div class="u-empty">기존 기록은 안전하게 유지되고 있습니다. 잠시 후 다시 확인해 주세요.</div>';
  $('resultCount').textContent='자료 검증 대기';
 }
}
function init(){
 $('compareForm').addEventListener('submit',e=>{
  e.preventDefault();compareShown=true;renderCompare();
 });
 $('compareReset').addEventListener('click',()=>{
  $('compareCodeA').value='';$('compareCodeB').value='';
  compareShown=false;$('compareResults').hidden=true;
  $('compareResults').innerHTML='';
  $('compareStatus').textContent='정확한 종목명이나 6자리 코드를 입력하면 두 종목을 비교할 수 있습니다.';
 });
 for(const id of ['compareCodeA','compareCodeB'])
  $(id).addEventListener('input',()=>{
   compareShown=false;$('compareResults').hidden=true;
   $('compareStatus').textContent='입력이 변경됐습니다. 비교하기를 눌러 결과를 갱신하세요.';
  });
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
 $('coverageGroup').addEventListener('change',()=>{limit=20;draw();});
 $('reload').addEventListener('click',load);
 $('more').addEventListener('click',()=>{limit+=20;draw();});
 $('top100Only').addEventListener('click',()=>{
   top100Only=!top100Only;limit=20;draw();
 });
 $('personalRank').addEventListener('click',()=>{
   sort=sort==='personal'?'stable':'personal';
   $('sort').value=sort;limit=20;top100Only=false;tab='all';
   for(const b of $('tabs').querySelectorAll('[data-tab]'))
     b.setAttribute('aria-pressed',String(b.dataset.tab==='all'));
   draw();
 });
 $('obvLagOnly').addEventListener('click',()=>{
   obvOnly=!obvOnly;limit=20;
   if(obvOnly){
     top100Only=false;tab='all';
     for(const b of $('tabs').querySelectorAll('[data-tab]'))
       b.setAttribute('aria-pressed',String(b.dataset.tab==='all'));
   }
   draw();
 });
 $('stockList').addEventListener('click',e=>{
  const b=e.target.closest('[data-stock]');if(!b)return;
  selected=b.dataset.stock;draw();
 });
 load();
}
D.addEventListener('DOMContentLoaded',init);
})(typeof window!=='undefined'?window:globalThis);
