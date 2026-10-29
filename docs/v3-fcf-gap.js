/* Source-only FCF missing-account diagnostics; never infer 0 CAPEX. */
(function () {
  'use strict';
  const el = document.getElementById('fcfMissingness');
  if (!el) return;
  const url = 'v3-dart-cashflow-gap-summary.json';
  const summary = 'v3-current-observed-all-listed-summary.json';
  const fmt = n => n.toLocaleString('ko-KR');
  const count = (n, max) => Number.isSafeInteger(n) && n >= 0 && n <= max;
  const validDate = text => {
    if (typeof text !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
    const t = Date.parse(text + 'T00:00:00Z');
    return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === text;
  };
  async function read(path) {
    const response = await fetch(path, {cache: 'no-store'});
    if (!response.ok) throw new Error('source unavailable');
    return response.json();
  }
  async function load() {
    el.textContent = '실제 공시자료 누락 원인을 대조 중…';
    try {
      const [gap, live] = await Promise.all([read(url), read(summary)]);
      const total = gap.listed_issuer_count;
      const d = gap.three_year_issuer_depth;
      const c = gap.categories;
      const depthKeys = [
        'three_year_source_receipt_complete',
        'three_years_attempted_missing_accounts',
        'saved_incomplete_years',
        'no_saved_cashflow_years',
        'three_years_with_fcf_but_unmatched_receipts'
      ];
      const classes = [
        'missing_both_capex', 'missing_intangible_only', 'missing_ppe_only',
        'missing_cfo_and_capex', 'missing_cfo_only',
        'fcf_observed_receipt_matched', 'fcf_observed_receipt_unmatched'
      ];
      if (!validDate(gap.asof) || gap.asof !== live.asof ||
          !count(total, 6000) || total < 100 || live.listed_total !== total ||
          gap.schema !== 'v3-annual-dart-fcf-missingness-source-audit-0.1' ||
          gap.historical_pit_verified !== false ||
          gap.missing_ifrs_account_not_interpreted_as_zero !== true ||
          gap.trading_score_promoted !== false ||
          !d || !c || depthKeys.some(k => !count(d[k], total)) ||
          depthKeys.reduce((sum, k) => sum + d[k], 0) !== total ||
          !count(gap.attempted_company_years, total * 3) ||
          !count(gap.observed_fcf_company_years, gap.attempted_company_years) ||
          !count(gap.missing_fcf_company_years, gap.attempted_company_years) ||
          classes.some(k => !count(c[k], gap.attempted_company_years)) ||
          classes.reduce((sum, k) => sum + c[k], 0) !== gap.attempted_company_years ||
          c.fcf_observed_receipt_matched + c.fcf_observed_receipt_unmatched !==
            gap.observed_fcf_company_years ||
          gap.observed_fcf_company_years + gap.missing_fcf_company_years !==
            gap.attempted_company_years ||
          d.three_years_with_fcf_but_unmatched_receipts !== 0) {
        throw new Error('non-matching saved source audit');
      }
      el.textContent =
        '동일 연도·범위·접수번호로 3개년 FCF 확인: ' +
        fmt(d.three_year_source_receipt_complete) + '/' + fmt(total) + '종목. ' +
        '미확보 종목: 3개년 조회 후 필수 계정 누락 ' +
        fmt(d.three_years_attempted_missing_accounts) + ', ' +
        '연도 일부 미수집 ' + fmt(d.saved_incomplete_years) + ', ' +
        '저장된 현금흐름 자료 없음 ' + fmt(d.no_saved_cashflow_years) + '. ' +
        '기업·연도별 결측: CAPEX 두 계정 모두 없음 ' + fmt(c.missing_both_capex) +
        ', 무형 CAPEX만 없음 ' + fmt(c.missing_intangible_only) +
        ', 유형 CAPEX만 없음 ' + fmt(c.missing_ppe_only) +
        ', CFO 및 CAPEX 동반 누락 ' + fmt(c.missing_cfo_and_capex) +
        ', CFO만 없음 ' + fmt(c.missing_cfo_only) + '. ' +
        '기준일 ' + gap.asof + ' · 없는 계정은 0원으로 처리하지 않으며, 투자점수 인증도 아닙니다.';
    } catch (_) {
      el.textContent =
        'FCF 결측 원인의 기준일 또는 합계가 일치하지 않아 표시를 보류합니다. ' +
        '자료 갱신 후 다시 확인해 주세요. 결측은 0원으로 대체하지 않습니다.';
    }
  }
  document.getElementById('reload')?.addEventListener('click', load);
  load();
})();
