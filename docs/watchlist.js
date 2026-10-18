/* Up to 20 research-only candidates using existing v2.2 observations.
 * NOT a v3 six-pillar score, investment recommendation, or re-ranking of
 * missing institutional/financial evidence. No fetch, credentials, or new API.
 * Browser: window.V22Watchlist; Node offline tests: module.exports.
 */
(function (root) {
  'use strict';
  const MAX_LISTED = 20;
  const MAX_FEATURED = 5;
  const EARLY = new Set(['초기 돌파 확인 중', '장기 추세 전환 관찰', '상승 후 수렴']);
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const missingRisk = row => Array.isArray(row.risk_flags) && row.risk_flags.some(
    x => typeof x === 'string' && /과대|급등/.test(x));

  function candidate(row) {
    if (!row || typeof row !== 'object' ||
        !/^\d{6}$/.test(String(row.code || '')) ||
        typeof row.name !== 'string' || !row.name.trim() ||
        !EARLY.has(row.phase) ||
        !numeric(row.price) || row.price <= 0 ||
        !numeric(row.stop_line) || row.stop_line <= 0 ||
        row.stop_line >= row.price ||
        !numeric(row.stop_pct) || row.stop_pct < -15 || row.stop_pct > -3 ||
        !numeric(row.reward_risk) || row.reward_risk < 1.5 ||
        !numeric(row.coverage_pct) || row.coverage_pct < 60 ||
        missingRisk(row)) return false;
    // Both the displayed stop percent and price/stop reference must agree.
    // Avoid showcasing corrupted records or contradictory stop levels.
    const observedStopPct = 100 * (row.stop_line / row.price - 1);
    if (Math.abs(observedStopPct - row.stop_pct) > 1.0) return false;
    return Boolean(row.obv_rising === true ||
      (Array.isArray(row.patterns) && row.patterns.length));
  }

  function orderKey(row) {
    const structures = Array.isArray(row.patterns) ? row.patterns : [];
    const preferred = structures.some(p => typeof p === 'string' && /쐐기|어깨|삼각|수렴/.test(p));
    const chase = (numeric(row.price20) && row.price20 > 12) ||
      (numeric(row.price60) && row.price60 > 25) ? 1 : 0;
    return {
      chase,
      preferred: preferred ? 1 : 0,
      obv: row.obv_rising === true ? 1 : 0,
      stop: Math.abs(row.stop_pct),
      // Existing unverified v2 score is deliberately NOT used as ranking.
      code: String(row.code)
    };
  }

  function compare(a, b) {
    const x = orderKey(a), y = orderKey(b);
    if (x.chase !== y.chase) return x.chase - y.chase;
    if (x.preferred !== y.preferred) return y.preferred - x.preferred;
    if (x.obv !== y.obv) return y.obv - x.obv;
    if (x.stop !== y.stop) return x.stop - y.stop;
    return x.code.localeCompare(y.code);
  }

  function isFresh(asof, now = new Date()) {
    if (typeof asof !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(asof)) return false;
    const closeKst = new Date(asof + 'T18:30:00+09:00');
    const elapsed = now.getTime() - closeKst.getTime();
    return Number.isFinite(elapsed) && elapsed >= 0 && elapsed <= 6 * 86400000;
  }

  function select(snapshot, now = new Date()) {
    if (!snapshot || snapshot.status !== 'success' ||
        !Array.isArray(snapshot.stocks) || !isFresh(snapshot.date, now)) {
      return {status: 'stale_or_unavailable', rows: [], featured_count: 0};
    }
    const used = new Set();
    const rows = snapshot.stocks.filter(candidate).sort(compare).filter(row => {
      if (used.has(row.code)) return false;
      used.add(row.code);
      return true;
    }).slice(0, MAX_LISTED);
    return {
      status: rows.length ? 'technical_observation_only' : 'no_eligible_observations',
      rows,
      featured_count: Math.min(MAX_FEATURED, rows.length),
      additional_count: Math.max(0, rows.length - MAX_FEATURED)
    };
  }

  const api = {candidate, isFresh, select, MAX_LISTED, MAX_FEATURED};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.V22Watchlist = api;
})(typeof window === 'undefined' ? {} : window);
