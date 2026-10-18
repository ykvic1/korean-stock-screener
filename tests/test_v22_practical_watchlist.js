'use strict';
// All synthetic: no API/network and no investment recommendation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const w = require('../docs/watchlist.js');

const now = new Date('2026-10-18T10:30:00+09:00');
const base = (code, extra = {}) => ({
  code, name: '가상종목-' + code,
  price: 10000, stop_line: 9400, stop_pct: -6,
  phase: '장기 추세 전환 관찰', obv_rising: true,
  patterns: [], reward_risk: 2.5, coverage_pct: 70,
  price20: 2, price60: 5, risk_flags: ['기관 수급 미확인'],
  // Legacy score is not part of the ranking:
  score: 99,
  ...extra
});

const candidateRows = Array.from({length: 25}, (_, i) =>
  base(String(100000 + i), {
    stop_line: 9600 - 15 * i, stop_pct: -4 - 0.15 * i,
    patterns: i%3 === 0 ? ['상승 후 수렴 후보'] : []
  }));
const snapshot = rows => ({status: 'success', date: '2026-10-16', stocks: rows});

assert.equal(w.select(snapshot(candidateRows), now).status, 'technical_observation_only');
assert.equal(w.MAX_LISTED, 20);
assert.equal(w.MAX_FEATURED, 5);
assert.equal(w.select(snapshot(candidateRows), now).rows.length, 20);
assert.equal(w.select(snapshot(candidateRows), now).featured_count, 5);
assert.equal(w.select(snapshot(candidateRows), now).additional_count, 15);
assert.equal(new Set(w.select(snapshot(candidateRows), now).rows.map(x=>x.code)).size, 20);
assert.deepEqual(w.select(snapshot(candidateRows.slice(0, 8)), now).rows.length, 8);

for (const bad of [
  {stop_pct: -25, stop_line: 7500},
  {stop_pct: -1, stop_line: 9900},
  {stop_line: 10100},
  {stop_pct: -5, stop_line: 7000},
  {reward_risk: 0.8},
  {coverage_pct: 40},
  {risk_flags: ['단기 급등 — 추격 주의']},
  {risk_flags: ['예상 손절폭 과대']},
  {price: null},
  {stop_line: null},
  {phase: '추세 상승 중'},
  {phase: '고점권'},
  {obv_rising: false, patterns: []},
  {code: '12345'},
  {name: ''}
]) {
  assert.equal(w.candidate(base('252990', bad)), false, JSON.stringify(bad));
}
assert.equal(w.candidate(base('252990')), true);
assert.equal(w.candidate(base('252990', {
  phase: '상승 후 수렴', obv_rising: false, patterns: ['상승 후 수렴 후보']
})), true);

assert.equal(w.select(snapshot([base('252990')]), new Date('2026-10-26T00:00:00+09:00')).status,
             'stale_or_unavailable');
assert.equal(w.select({status: 'failed', date: '2026-10-16', stocks: [base('252990')]}, now).rows.length, 0);
assert.equal(w.select(snapshot([base('252990', {stop_pct: -26, stop_line: 7400})]), now).status,
             'no_eligible_observations');
assert.deepEqual(w.select(snapshot([base('252990'), base('252990')]), now).rows.map(r=>r.code),
                 ['252990']);

// Changes to the unverified legacy v2 score must not affect shortlist order.
const first = base('252990', {score: 3, patterns: ['하락쐐기 근사 후보']});
const second = base('112610', {score: 99, stop_line: 9600, stop_pct: -4});
const rank = w.select(snapshot([first, second]), now).rows.map(r=>r.code);
assert.deepEqual(rank, ['252990', '112610']);

assert.equal(w.select(snapshot([
  base('252990', {score: 0}),
  base('112610', {score: 99, stop_line: 9600, stop_pct: -4})
]), now).rows.length, 2);

// Real existing data is only checked for safe result shape, never
// assumed to have all 6 v3 pillars or confirmed investor flows.
const actual = JSON.parse(fs.readFileSync(path.join(__dirname, '../docs/data.json'), 'utf8'));
if (actual.status === 'success' && Array.isArray(actual.stocks) && actual.date) {
  const date = new Date(actual.date + 'T18:30:00+09:00');
  const result = w.select(actual, new Date(date.getTime() + 12 * 3600000));
  assert.ok(result.rows.length <= 20);
  for (const row of result.rows) {
    assert.ok(w.candidate(row));
    assert.ok(row.stop_pct >= -15 && row.stop_pct <= -3);
    assert.ok(row.reward_risk >= 1.5);
  }
}

// Static sanity of integration and escaping mechanism: do not change v2 model.
const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
assert.ok(html.includes('<script src="watchlist.js"></script>'));
assert.ok(html.includes('renderPractical(d);'));
assert.ok(html.includes('esc(r.name)'));
assert.ok(html.includes('매수 추천이 아닙니다'));
assert.ok(html.includes('관심 종목 최대 20개'));
assert.ok(html.includes('우선 관찰'));
assert.ok(html.includes('추가 관찰'));
console.log('practical_watchlist_offline_tests_passed');
