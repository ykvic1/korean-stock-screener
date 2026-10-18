/* Research-only coverage-aware rating for existing v2.2 issued snapshots.
 * Never uses a missing field as zero. Derives from observed components and
 * published weight contract (both must be present). NOT the v3 six-pillar IAS
 * score; no price, flow or financial data are invented here.
 */
(function (root) {
  'use strict';
  const digit = x => typeof x === 'number' && Number.isFinite(x);
  function tenthsTruncate(x) {
    return Math.floor((x + Number.EPSILON) * 10) / 10;
  }
  function rating(row, weights) {
    if (!row || typeof row !== 'object' ||
        !row.components || typeof row.components !== 'object' ||
        !weights || typeof weights !== 'object') return null;
    const factors = Object.entries(weights);
    if (!factors.length || factors.length > 30) return null;
    const full = factors.reduce((sum, [,w]) => sum + (digit(w) ? w : NaN), 0);
    if (!digit(full) || Math.abs(full - 100) > 0.00001) return null;
    let observed = 0, available = 0, missing = [];
    for (const [key, weight] of factors) {
      if (!digit(weight) || weight <= 0 ||
          !Object.prototype.hasOwnProperty.call(row.components, key)) return null;
      const value = row.components[key];
      if (value === null) {
        missing.push({name:key, weight});
        continue;
      }
      if (!digit(value) || value < 0 || value > weight) return null;
      observed += value;
      available += weight;
    }
    if (available <= 0) return null;
    // Explicit one-decimal truncation for easy-to-audit examples:
    // 70 / 85 * 100 = 82.3529 -> 82.3.
    const score = tenthsTruncate(observed / available * 100);
    return {
      normalized: score,
      earned: Math.round(observed * 1000000)/1000000,
      available,
      missing,
      coverage: tenthsTruncate(available/full*100),
      scale: 'legacy-v2-observed-only',
      provisional: true
    };
  }
  const api = {rating, tenthsTruncate};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PartialScoreView = api;
})(typeof window === 'undefined' ? {} : window);
