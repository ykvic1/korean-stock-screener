/* Main-site investor watchlist: ONE score-based ranking + independent risk
 * and observation-horizon labels. Based exclusively on published v2.2
 * observations. NOT future-return predictions or verified v3 IAS scoring.
 */
(function (root) {
  'use strict';
  const MAX_LISTED = 20;
  const MAX_FEATURED = 5;
  const numeric = x => typeof x === 'number' && Number.isFinite(x);
  const HORIZONS = Object.freeze({
    '1m':'단기 · 1개월', '3m':'중기 · 3개월',
    '6m':'장기 · 6개월 이상', 'unknown':'관찰 기간 미확인'
  });
  const KINDS = Object.freeze({
    'low-high':'손절폭 작음 · 과거 고점거리 큼',
    'low-low':'손절폭 작음 · 과거 고점거리 작음',
    'high-high':'손절·환금 위험 큼 · 과거 고점거리 큼',
    'high-low':'손절·환금 위험 큼 · 과거 고점거리 작음',
    'balanced':'중간 위험·상승공간 / 혼합',
    'unknown':'위험·상승공간 분류 미확인'
  });
  function profile(row) {
    if (!row || typeof row!=='object') {
      return {risk:'unknown', upside:'unknown', kind:'unknown', horizon:'unknown',
        label:KINDS.unknown, horizonLabel:HORIZONS.unknown, reasoning:'자료 미확인'};
    }
    const price=numeric(row.price)&&row.price>0?row.price:null;
    const stop=numeric(row.stop_line)&&row.stop_line>0?row.stop_line:null;
    const stopValid=price!==null&&stop!==null&&stop<price&&
      numeric(row.stop_pct)&&row.stop_pct<0 &&
      Math.abs(100*(stop/price-1)-row.stop_pct)<=1.0;
    // A corrupted or missing price/stop pairing is not "low-risk".
    const distance=stopValid?-row.stop_pct:null;
    const flags=Array.isArray(row.risk_flags)?row.risk_flags:[];
    const severe=flags.some(x=>typeof x==='string' &&
      /손절폭 과대|장기 추세 취약|단기 급등|추격 주의/.test(x));
    const thin=numeric(row.trading_eok)&&row.trading_eok<2;
    let risk=distance===null?'unknown':distance<3||distance>15||severe||thin?'high':
      distance<=8?'low':'middle';
    // The upside reference is NOT expected return; it is simply the
    // distance to an earlier 52-week high/resistance.
    const headroom=numeric(row.reference_target_pct)?row.reference_target_pct:null;
    const rr=numeric(row.reward_risk)?row.reward_risk:null;
    const upside=headroom===null?'unknown':
      headroom>=25&&rr!==null&&rr>=1.5?'high':
      headroom<15||rr!==null&&rr<1?'low':'middle';

    let kind='balanced';
    if(risk==='unknown'||upside==='unknown')kind='unknown';
    else if(risk==='low'&&upside==='high')kind='low-high';
    else if(risk==='low'&&upside==='low')kind='low-low';
    else if(risk==='high'&&upside==='high')kind='high-high';
    else if(risk==='high'&&upside==='low')kind='high-low';

    const phase=String(row.phase||'');
    const patterns=Array.isArray(row.patterns)?row.patterns:[];
    const signals=Array.isArray(row.signals)?row.signals:[];
    let horizon='unknown',reasoning='일봉·주봉 전환 근거 미충분';
    if(phase==='장기 추세 전환 관찰'||signals.includes('주봉 120주선 회복')) {
      horizon='6m'; reasoning='주봉 장기 추세 전환 신호';
    } else if(phase==='상승 후 수렴'||patterns.some(p=>
      typeof p==='string' && /수렴|삼각|쐐기|역헤드|어깨/.test(p))) {
      horizon='3m'; reasoning='차트 패턴 형성 및 수렴 구간';
    } else if(phase==='초기 돌파 확인 중') {
      horizon='1m'; reasoning='일봉 초기 돌파 관찰';
    }
    return {
      risk,upside,kind,horizon,
      label:KINDS[kind],horizonLabel:HORIZONS[horizon],reasoning,
      stopDistancePct:distance, historicalHeadroomPct:headroom,
      rewardRiskReference:rr,
      expectedReturnVerified:false,
      timeForecastVerified:false
    };
  }

  // Do not drop higher-risk shares from the TOP-20 SCORE list: highlight
  // their risk instead. No fictitious 0 when a pillar is missing.
  function candidate(row) {
    const rating=row?._rating;
    return Boolean(row && typeof row==='object' &&
      /^\d{6}$/.test(String(row.code||'')) &&
      typeof row.name==='string'&&row.name.trim() &&
      numeric(row.price)&&row.price>0 &&
      rating && numeric(rating.normalized) &&
      rating.normalized>=0&&rating.normalized<=100 &&
      numeric(rating.coverage)&&rating.coverage>0&&rating.coverage<=100 &&
      numeric(rating.available)&&rating.available>0&&rating.available<=100);
  }

  function compare(a,b) {
    const ar=a._rating,br=b._rating;
    return br.normalized-ar.normalized ||
      br.coverage-ar.coverage ||
      br.available-ar.available ||
      String(a.code).localeCompare(String(b.code));
  }

  function isFresh(asof,now=new Date()) {
    if(typeof asof!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(asof))return false;
    const end=new Date(asof+'T18:30:00+09:00');
    const elapsed=now.getTime()-end.getTime();
    return Number.isFinite(elapsed)&&elapsed>=0&&elapsed<=6*86400000;
  }

  function select(snapshot,now=new Date(),filter={}) {
    if(!snapshot||snapshot.status!=='success'||
       !Array.isArray(snapshot.stocks)||!isFresh(snapshot.date,now)){
      return {status:'stale_or_unavailable',rows:[],featured_count:0,
        additional_count:0,eligible_count:0,filtered_count:0};
    }
    const kind=filter.kind||'all',horizon=filter.horizon||'all';
    if(!['all',...Object.keys(KINDS)].includes(kind) ||
       !['all',...Object.keys(HORIZONS)].includes(horizon)){
      return {status:'invalid_filter',rows:[],featured_count:0,
        additional_count:0,eligible_count:0,filtered_count:0};
    }
    const used=new Set();
    const scored=snapshot.stocks.filter(candidate).sort(compare).filter(row=>{
      if(used.has(row.code))return false;
      used.add(row.code);return true;
    });
    const qualifying=scored.filter(row=>{
      const p=profile(row);
      return (kind==='all'||p.kind===kind)&&
        (horizon==='all'||p.horizon===horizon);
    });
    const rows=qualifying.slice(0,MAX_LISTED);
    return {
      status: rows.length?'score_ranked_research_only':'no_matching_observations',
      rows,featured_count:Math.min(MAX_FEATURED,rows.length),
      additional_count:Math.max(0,rows.length-MAX_FEATURED),
      eligible_count:scored.length,filtered_count:qualifying.length,
      selected_kind:kind,selected_horizon:horizon
    };
  }

  const api={MAX_LISTED,MAX_FEATURED,profile,candidate,compare,
    isFresh,select,KINDS,HORIZONS};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.V22Watchlist=api;
})(typeof window==='undefined'?{}:window);
