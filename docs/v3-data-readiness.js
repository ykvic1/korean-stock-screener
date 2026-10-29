/* Read-only, source-dated V3 coverage: never convert missing data into scores. */
(function () {
  'use strict';
  const SOURCE_FILES = [
    'v3-current-observed-all-listed-summary.json',
    'v3-real-source-six-area-coverage.json',
    'v3-all-listed-scorecard-summary.json'
  ];
  const ids = {
    status: document.getElementById('readinessStatus'),
    financial: document.getElementById('readinessFinancial'),
    price: document.getElementById('readinessPrice'),
    cashflow: document.getElementById('readinessCashflow'),
    certified: document.getElementById('readinessCertified'),
    notes: document.getElementById('readinessNotes')
  };
  if (Object.values(ids).some(x => !x)) return;
  const fmt = value => value.toLocaleString('ko-KR');
  const count = (n, max) => Number.isSafeInteger(n) && n >= 0 && n <= max;
  const date = text => {
    if (typeof text !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
    const stamp = Date.parse(text + 'T00:00:00Z');
    return Number.isFinite(stamp) &&
      new Date(stamp).toISOString().slice(0, 10) === text;
  };
  const percent = (value, total) => (100 * value / total).toFixed(1) + '%';
  const reset = () => {
    for (const name of ['financial', 'price', 'cashflow', 'certified']) {
      ids[name].textContent = '미확인';
    }
  };
  async function load() {
    reset();
    ids.status.textContent = '실제 수집자료 확인 중…';
    ids.notes.textContent = '자료 기준일과 종목 수가 일치하는지 검증 중입니다.';
    try {
      // Source-specific freshness: a lagging weekly scorecard must not hide
      // independently verified SAME-DAY DART and price source coverage.
      const [current, history] = await Promise.all(SOURCE_FILES.slice(0, 2).map(async path => {
        const response = await fetch(path, {cache: 'no-store'});
        if (!response.ok) throw new Error('source unavailable');
        return response.json();
      }));
      let score = null;
      try {
        const response = await fetch(SOURCE_FILES[2], {cache: 'no-store'});
        if (response.ok) score = await response.json();
      } catch (_) { /* Optional FCF source may legitimately lag. */ }
      const asof = current.asof;
      const listed = current.listed_total;
      if (!date(asof) || asof !== history.market_asof ||
          !Number.isSafeInteger(listed) || listed < 100 ||
          listed !== history.listed_total ||
          !current.observed_issuer_counts || !history.historical_dart_company_years_attempted_by_fiscal ||
          !history.historical_dart_company_years_with_accounts_by_fiscal) {
        throw new Error('source date or market census mismatch');
      }
      const fiscalYears = Array.from({length: 4}, (_, i) => String(Number(asof.slice(0, 4)) - 4 + i));
      let attempted = 0, actual = 0;
      for (const year of fiscalYears) {
        const a = history.historical_dart_company_years_attempted_by_fiscal[year];
        const b = history.historical_dart_company_years_with_accounts_by_fiscal[year];
        if (!count(a, listed) || !count(b, a)) throw new Error('invalid fiscal coverage');
        attempted += a;
        actual += b;
      }
      const priced = current.observed_issuer_counts.technical;
      // FCF must be same-market-day, but never suppress financial/price
      // coverage just because the independently scheduled FCF report lags.
      const fcf = score && score.asof === asof && score.listed_total === listed &&
        score.observed_source_depth ?
        score.observed_source_depth.three_year_receipt_matched_fcf_issuers : null;
      const certified = current.certified_100point_count;
      // Public aggregate JSON is not independent proof of a certified score.
      // Fail closed if a future publisher accidentally flips certification
      // before a separately verified source-chain gate is implemented.
      if (certified !== 0 ||
          current.independent_krx_adjusted_path_certification !== false ||
          current.not_an_investment_ranking !== true ||
          (score && score.asof === asof && score.listed_total === listed &&
           score.full_six_pillar_certified_count !== 0)) {
        throw new Error('independent score certification not established');
      }
      if (attempted < 1 || !count(priced, listed) ||
          (fcf !== null && !count(fcf, listed)) || !count(certified, listed) ||
          !count(current.observed_any_factor_issuers, listed)) {
        throw new Error('invalid source-derived coverage');
      }
      ids.financial.textContent = fmt(actual) + ' / ' + fmt(attempted);
      ids.price.textContent = fmt(priced) + ' / ' + fmt(listed);
      ids.cashflow.textContent = fcf === null ? '미확인' : fmt(fcf) + ' / ' + fmt(listed);
      ids.certified.textContent = fmt(certified) + '종목';
      ids.status.textContent = '자료 기준일 ' + asof;
      ids.notes.textContent =
        'DART 4개년 재무 ' + percent(actual, attempted) +
        ' · 기술분석 관측 ' + percent(priced, listed) +
        (fcf === null ? ' · 3개년 FCF 갱신 대기' :
          ' · 3개년 FCF ' + percent(fcf, listed)) +
        '. 이는 데이터 확보율이며 매수 검증률이 아닙니다. ' +
        '정식 투자점수는 독립 인증 전이며 미확보 항목은 0점으로 처리하지 않습니다.';
    } catch (_) {
      reset();
      ids.status.textContent = '출처 재검증 중';
      ids.notes.textContent =
        '수집 결과의 기준일이나 종목 수가 다르거나 파일을 읽을 수 없습니다. ' +
        '서로 다른 날짜의 수치를 합산하거나 미확인을 0으로 표시하지 않습니다.';
    }
  }
  document.getElementById('reload')?.addEventListener('click', load);
  load();
})();
