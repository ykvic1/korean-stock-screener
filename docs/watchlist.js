/* INVESTMENT DECISION DRAFT — technical setup + conservative risk + optional
 * historical analogue evidence. Do NOT confuse 52-week-high gap with future
 * return, or past conditional returns with a forward-looking expectation.
 *
 * v2 has NO verified corporate financial/industry inputs, and investor flows
 * are unavailable for most stocks. Recommendations can therefore be
 * "priority for further analysis", NEVER an automatic confirmed buy.
 */
(function (root) {
  'use strict';
  const MAX_LISTED=20,MAX_FEATURED=5;
  const numeric=x=>typeof x==='number'&&Number.isFinite(x);
  const HORIZONS=Object.freeze({
    '1m':'단기 접근 · 약 1개월',
    '3m':'중기 접근 · 약 3개월',
    '6m':'장기 검토 · 6개월 이상',
    'unknown':'적정 보유기간 판단 보류'
  });
  const RISKS=Object.freeze({
    low:'차트 손절위험 낮음',middle:'차트 손절위험 중간',
    high:'손절·추격·환금위험 높음',unknown:'손실위험 자료 미확인'
  });
  const QUANT_PERIODS=Object.freeze({
    '1m':'스윙 1개월','3m':'중기 3개월','6m':'중기 6개월'
  });
  const quantNumber=n=>numeric(n)?n:null;

  function historicalPeriods(study,marketDate) {
    const out={};
    const usable=study&&typeof study==='object'&&study.periods &&
      study.asof===marketDate;
    for(const [h,label] of Object.entries(QUANT_PERIODS)) {
      const x=usable?study.periods[label]:null;
      // Past-neighbor results from surviving stocks are exploratory, even
      // if there are >40 historical cases, >10 symbols and >12 quarters.
      const enough=x?.status==='exploratory' &&
        numeric(x.sample_n)&&x.sample_n>=40 &&
        numeric(x.symbol_n)&&x.symbol_n>=10 &&
        numeric(x.quarter_n)&&x.quarter_n>=12 &&
        numeric(x.mean_net_return_pct)&&numeric(x.stop_first_pct) &&
        numeric(x.max_adverse_p75_pct);
      out[h]={
        status:enough?'exploratory':'unavailable',
        meanHistoricalNetPct:enough?x.mean_net_return_pct:null,
        historicalStopFirstPct:enough?x.stop_first_pct:null,
        historicalAdverseP75Pct:enough?x.max_adverse_p75_pct:null,
        sampleN:enough?x.sample_n:null,
        validatedForwardPrediction:false
      };
    }
    return out;
  }

  function profile(row,study=null,marketDate=null) {
    const empty={
      risk:'unknown',riskLabel:RISKS.unknown,
      horizon:'unknown',horizonLabel:HORIZONS.unknown,
      reasoning:'데이터 부족으로 기간 판단 보류',
      stopDistancePct:null,expectedReturnPct:null,
      expectedReturnVerified:false,
      highExpectedReturn:false,
      forecastRiskReturnKind:'unverified',
      recommendation:'추천 판단 보류',
      recommendationReason:'수급·재무·업황 및 손절 가능성을 검증해야 합니다.',
      financialValidated:false,flowsValidated:false,
      historical:historicalPeriods(null,null),
      longHoldingCaution:null
    };
    if(!row||typeof row!=='object')return empty;
    const price=numeric(row.price)&&row.price>0?row.price:null;
    const stop=numeric(row.stop_line)&&row.stop_line>0?row.stop_line:null;
    const consistentStop=price!==null&&stop!==null&&stop<price&&
      numeric(row.stop_pct)&&row.stop_pct<0 &&
      Math.abs(100*(stop/price-1)-row.stop_pct)<=1.0;
    const distance=consistentStop?-row.stop_pct:null;
    const flags=Array.isArray(row.risk_flags)?row.risk_flags:[];
    const dangerous=flags.some(f=>typeof f==='string' &&
      /손절폭 과대|장기 추세 취약|단기 급등|추격 주의|급격한 거래량 집중/.test(f));
    const illiquid=numeric(row.trading_eok)&&row.trading_eok<2;
    const risk=distance===null?'unknown':
      (distance<3||distance>15||dangerous||illiquid)?'high':
      distance<=8?'low':'middle';

    const qt=historicalPeriods(study,marketDate);
    const p1=qt['1m'],p3=qt['3m'],p6=qt['6m'];
    const enough=h=>h.status==='exploratory';
    const shortOnly=enough(p1)&&enough(p3)&&
      p1.meanHistoricalNetPct>0&&p3.meanHistoricalNetPct<=0;
    const mediumOnly=enough(p3)&&enough(p6)&&
      p3.meanHistoricalNetPct>0&&p6.meanHistoricalNetPct<=0;
    const weakLong=(numeric(row.ma120w_gap)&&row.ma120w_gap<-15)||
      flags.includes('장기 추세 취약');
    const phase=String(row.phase||'');
    const patterns=Array.isArray(row.patterns)?row.patterns:[];
    const signals=Array.isArray(row.signals)?row.signals:[];
    let horizon='unknown',reasoning='근거가 부족해 단기·중기·장기를 정하지 않았습니다.';
    let longHoldingCaution=null;

    // A short-term positive past analogue accompanied by a negative longer
    // analogue is a WARNING against extending the holding period, NOT proof
    // of a positive next-month forecast.
    if(shortOnly) {
      horizon='1m';
      reasoning='유사조건 과거 1개월 수익이 3개월보다 우세해 단기 관찰 (미래 검증 전)';
      longHoldingCaution='과거 3개월 조건부 순수익 부진 · 장기 보유 주의';
    } else if(mediumOnly) {
      horizon='3m';
      reasoning='유사조건 과거 3개월 수익이 6개월보다 우세해 중기 관찰 (미래 검증 전)';
      longHoldingCaution='과거 6개월 조건부 순수익 부진 · 장기 보유 주의';
    } else if(weakLong && (phase==='초기 돌파 확인 중'||row.is_breakout===true)) {
      horizon='1m';
      reasoning='일봉 반등/돌파 관찰, 주봉 장기 추세 취약';
      longHoldingCaution='주봉 약세로 장기 보유 위험 점검 필요';
    } else if(phase==='초기 돌파 확인 중'||signals.includes('거래량 동반 초기 돌파')) {
      horizon='1m';reasoning='거래량과 일봉 초기 돌파 중심의 단기 대응 가설';
    } else if(phase==='상승 후 수렴'||patterns.some(p=>
      typeof p==='string'&&/삼각|쐐기|역헤드|어깨|수렴/.test(p))) {
      horizon='3m';reasoning='일봉 조정·수렴 구조에 대한 중기 대응 가설';
    } else if(!weakLong && (phase==='장기 추세 전환 관찰'||
      signals.includes('주봉 120주선 회복')||row.weekly_turn===true)) {
      horizon='6m';
      reasoning='주봉 장기 전환 관찰 대상 · 장기 보유에는 실적·업황 재검증 필수';
    }
    if(!longHoldingCaution && weakLong)
      longHoldingCaution='장기 추세 취약: 단기 반등이 이어져도 장기 보유는 별도 판단';

    const normalized=row._rating?.normalized;
    const coverage=row._rating?.coverage;
    const confirmedFinancial=row.financial_confirmed===true;
    // A raw Npay/pykrx field without verified session provenance cannot
    // make the investor pillar automatically confirmed.
    const confirmedFlows=row.flows_confirmed===true &&
      row.flow_session_status==='regular_session_verified';
    const plausible=consistentStop&&risk!=='high'&&
      numeric(normalized)&&normalized>=75&&
      numeric(coverage)&&coverage>=60&&horizon!=='unknown' &&
      !dangerous;
    const recommendation=plausible?'심층분석 우선 후보':
      risk==='high'?'위험 우선 점검':
      risk==='unknown'?'손절 근거 확인 후 판단':
      '조건부 관찰';
    const recommendationReason=plausible?
      '기술점수·데이터 확보율·손절 참고폭이 양호하나 재무·수급·업황 확인 전 매수 확정 불가':
      risk==='high'?'손절 거리·추격 또는 환금성 경고가 있어 진입 보류가 우선':
      '자료 부족 또는 기술점수/기간 근거가 미흡해 매수 우선추천은 보류';
    return {
      risk,riskLabel:RISKS[risk],horizon,horizonLabel:HORIZONS[horizon],
      reasoning,stopDistancePct:distance,
      expectedReturnPct:null,
      expectedReturnVerified:false,highExpectedReturn:false,
      forecastRiskReturnKind:'unverified', // NEVER use 52w gap as return
      recommendation,recommendationReason,
      financialValidated:confirmedFinancial,flowsValidated:confirmedFlows,
      historical:qt,longHoldingCaution,
      readyToBuy:false
    };
  }

  function candidate(row) {
    const a=row?._rating;
    return Boolean(row&&typeof row==='object' &&
      /^\d{6}$/.test(String(row.code||''))&&
      typeof row.name==='string'&&row.name.trim() &&
      numeric(row.price)&&row.price>0 &&
      a&&numeric(a.normalized)&&a.normalized>=0&&a.normalized<=100 &&
      numeric(a.coverage)&&a.coverage>0&&a.coverage<=100 &&
      numeric(a.available)&&a.available>0&&a.available<=100);
  }
  function compare(a,b) {
    const ar=a._rating,br=b._rating;
    return br.normalized-ar.normalized||
      br.coverage-ar.coverage||
      br.available-ar.available||
      String(a.code).localeCompare(String(b.code));
  }
  function isFresh(asof,now=new Date()) {
    if(typeof asof!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(asof))return false;
    const end=new Date(asof+'T18:30:00+09:00');
    const elapsed=now.getTime()-end.getTime();
    return Number.isFinite(elapsed)&&elapsed>=0&&elapsed<=6*86400000;
  }
  function select(snapshot,now=new Date(),filter={},quantSource=null) {
    if(!snapshot||snapshot.status!=='success'||
      !Array.isArray(snapshot.stocks)||!isFresh(snapshot.date,now)){
      return {status:'stale_or_unavailable',rows:[],featured_count:0,
        additional_count:0,eligible_count:0,filtered_count:0,priority:null};
    }
    const risk=filter.risk||'all',horizon=filter.horizon||'all';
    if(!['all',...Object.keys(RISKS)].includes(risk)||
      !['all',...Object.keys(HORIZONS)].includes(horizon)){
      return {status:'invalid_filter',rows:[],featured_count:0,
        additional_count:0,eligible_count:0,filtered_count:0,priority:null};
    }
    const byCode=new Map();
    if(quantSource?.asof===snapshot.date&&Array.isArray(quantSource.stocks)){
      for(const x of quantSource.stocks) {
        if(x&&/^\d{6}$/.test(String(x.code||'')))byCode.set(x.code,{...x,asof:quantSource.asof});
      }
    }
    const visited=new Set();
    const scored=snapshot.stocks.filter(candidate).sort(compare).filter(row=>{
      if(visited.has(row.code))return false;
      visited.add(row.code);return true;
    });
    const matching=scored.filter(row=>{
      const p=profile(row,byCode.get(row.code),snapshot.date);
      return (risk==='all'||p.risk===risk)&&
        (horizon==='all'||p.horizon===horizon);
    });
    const rows=matching.slice(0,MAX_LISTED);
    const review=matching.find(row=>profile(
      row,byCode.get(row.code),snapshot.date
    ).recommendation==='심층분석 우선 후보')||null;
    return {
      status:rows.length?'score_ranked_research_only':'no_matching_observations',
      rows,featured_count:Math.min(MAX_FEATURED,rows.length),
      additional_count:Math.max(0,rows.length-MAX_FEATURED),
      eligible_count:scored.length,filtered_count:matching.length,
      selected_risk:risk,selected_horizon:horizon,
      priority:review?{code:review.code,name:review.name,
        analysis:profile(review,byCode.get(review.code),snapshot.date)}:null,
      // Even if there is a priority for RESEARCH, there is no confirmed buy
      // recommendation because 6-pillar fundamentals and flows are incomplete.
      confirmedBuyRecommendation:null
    };
  }
  const api={MAX_LISTED,MAX_FEATURED,profile,candidate,compare,
    isFresh,select,RISKS,HORIZONS,QUANT_PERIODS,historicalPeriods};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.V22Watchlist=api;
})(typeof window==='undefined'?{}:window);
