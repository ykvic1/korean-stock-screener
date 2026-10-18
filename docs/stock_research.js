/* Research annotations for the main Korean-stock screener.
 * The v2.1 quant and v2.2 reliability studies were computed offline from a
 * limited historical sample. A stock outside that sample is NEVER marked as
 * verified, failed or financially weak. No browser-side data fabrication.
 * Safe to use on GitHub Pages without a private token or server.
 */
(function (root) {
  'use strict';
  const CODE = /^\d{6}$/;
  const num = x => typeof x === 'number' && Number.isFinite(x);
  const asofOk = x => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(x);

  function quality(code, audit, marketAsOf) {
    if (!CODE.test(String(code))) return {state:'invalid-code', label:'품질 평가 미확인'};
    if (!audit || !Array.isArray(audit.stocks) || !asofOk(audit.asof)) {
      return {state:'unavailable', label:'품질 자료 확인 중/사용 불가'};
    }
    const item=audit.stocks.find(x=>x && x.code === code);
    if (!item) {
      return {state:'not-studied', label:'품질 연구표본에 없음', asof:audit.asof};
    }
    if (marketAsOf && audit.asof!==marketAsOf) {
      return {state:'outdated', label:'품질 자료 기준일 다름', asof:audit.asof,
        strict: item.strict_valid===true, matched:item.independently_matched===true};
    }
    const strict=item.strict_valid===true;
    const matched=strict && item.independently_matched===true
      && item.cross_validation?.status==='matched_recent';
    const label=!strict?'가격 이력 품질 재확인 필요':
      matched?'가격 구간 양호 · 독립 대조 일치':'가격 구간 양호 · 독립 대조 미확인';
    return {
      state:!strict?'needs-review':matched?'matched':'partial',
      label, strict, matched, asof:audit.asof,
      usable:num(item.usable_segment_rows)?item.usable_segment_rows:null,
      quarantined:num(item.quarantined_rows)?item.quarantined_rows:null,
      cause: Array.isArray(item.cause_categories)?item.cause_categories.filter(x=>typeof x==='string').slice(0,5):[],
      official_session_verified:false
    };
  }

  const PERIODS=['초단기 1주','단기 2주','스윙 1개월','스윙 2개월',
                 '중기 3개월','중기 6개월','장기 1년'];
  function quant(code, study, marketAsOf) {
    if (!CODE.test(String(code))) return {state:'invalid-code',label:'퀀트 검증 불가',rows:[]};
    if (!study || !Array.isArray(study.stocks) || !asofOk(study.asof)) {
      return {state:'unavailable',label:'퀀트 연구자료 이용 불가',rows:[]};
    }
    const stock=study.stocks.find(x=>x && x.code===code);
    if (!stock) {
      return {state:'not-studied',label:'이 종목은 기존 퀀트 연구표본에 없음',rows:[],
        asof:study.asof, sampled:study.stocks.length};
    }
    const rows=PERIODS.map(period=>{
      const source=stock.periods && stock.periods[period];
      const p=source && typeof source==='object' ? source : {};
      const sampled=p.status==='exploratory';
      return {
        period, status:sampled?'exploratory':'insufficient',
        sample_n:num(p.sample_n)?p.sample_n:null,
        up_pct:sampled&&num(p.up_pct)?p.up_pct:null,
        mean_net_return_pct:sampled&&num(p.mean_net_return_pct)?p.mean_net_return_pct:null,
        stop_first_pct:sampled&&num(p.stop_first_pct)?p.stop_first_pct:null,
        mean_mae_pct:sampled&&num(p.mean_mae_pct)?p.mean_mae_pct:null,
        max_adverse_p75_pct:sampled&&num(p.max_adverse_p75_pct)?p.max_adverse_p75_pct:null,
        reason:sampled?null:(typeof p.reason==='string'?p.reason:'표본/독립검증 부족'),
      };
    });
    const available=rows.filter(x=>x.status==='exploratory').length;
    const outdated=Boolean(marketAsOf && marketAsOf!==study.asof);
    return {
      state:outdated?'outdated':available?'historical-observations':'insufficient',
      label:outdated?'이전 기준일 퀀트 결과 · 최신 검증 아님':
        available?'과거 유사조건 관측 자료':'연구표본은 있으나 검증 자료 부족',
      name:stock.name, asof:study.asof, rows,
      explored:available, sampled:study.stocks.length,
      suggestion:typeof stock.period_suggestion?.decision==='string'
        ?stock.period_suggestion.decision:'관망/추가 검증',
      official_session_verified:false,
      predictive_probability:false
    };
  }

  const api={quality,quant,PERIODS};
  if(typeof module!=='undefined'&&module.exports) module.exports=api;
  else root.StockResearch=api;
})(typeof window==='undefined'?{}:window);
