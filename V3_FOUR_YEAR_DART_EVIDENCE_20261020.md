# V3 next substantive step — four-year financial evidence, not annual-company count (2026-10-20)

## Observed current production gap
- Actual published rolling DART current-listed reference snapshot: source date **2026-10-19**, 2,497 listed, **202 attempted issuer references, 195 annual observations, 193 derived simple P/B references**. The amounts are **one latest annual year**, not a four-year profitability history.
- The existing `v3_financial_feature_adapter.calculate_financial_raw_features` needs **four consecutive fiscal years**, same source and CFS/OFS scope to calculate 3-year revenue CAGR, 3-year operating margin and 3-year FCF/assets. One-year annual references cannot become financial15 points, no matter how many issuers are covered.
- The currently bounded `official_data.dart_collect(...,years=1)` is scheduled eight times per weekday. Its optional deep research user route still defaults to 3 years. **Neither schedule nor existing default is changed.**

## New real-source bridge
1. A separate `v3_dart_four_year_reference.py` validates dated rolling one-year DART census, selects up to **4** currently-priced companies with observed annual filings, chooses the highest existing score first, and gathers 2022–25 (for a 2026 signal) reports through the existing OpenDART collector `years=4`. Provider calls occur **only in the manually dispatched live mode**, not on push or existing daily schedules.
2. Keeps current ticker identity, receipt chronology and four-year exact year sequence, *same* OFS or CFS, full KRW monetary unit and finite numbers. Partial or mixed data remain insufficient; older company reports in cache do not get silently counted as contemporaneous PIT-certified.
3. Safe derived 4-year research: **3-year revenue CAGR** and **3-year mean operating margin**; each is independently nullable. The current official DART extractor does NOT supply capital-expenditure, interest-bearing-debt or cash accounts and does NOT authorize a complete financial15, FCF yield, or real valuation15. The program deliberately leaves FCF/assets, net debt/equity, official exchange PBR and full100 investment rank **null**.
4. Output `docs/v3-dart-four-year-reference.json` contains only historical derived annual values and receipt labels; no raw filings, keys or log leaks. An atomic write is made only after a whole selected batch has completed and passed unit/date/census checks. Persisted records are reused when the next separate manual pilot is requested. A publication rebase must confirm the same last successful market day and live universe.
5. Manual workflow: `V3 Four-Year DART Financial Reference (manual pilot)`. Default **dry-run** (zero provider calls, zero repo mutations), alternative explicit **live** with at most 1/2/4 companies. It shares write concurrency with the existing data publisher. There is **no automatic trigger** and **no V2.2/V3 ranking/UI intervention**.

## Acceptance and risk
Unit tests reject lookahead receipts, mixed CFS/OFS, non-KRW, missing prior business year, NaN, overbudget batch, stale annual-book/census and incomplete network responses. A failed provider call never overwrites a known-good financial reference; the default scheduled 8x40 short-year collector stays untouched.

After four-year data is collected on a sufficient cross-section: normalize only actually observed features and verify a frozen point-in-time provider source chain, CAPEX/cash/debt accounts and peer counts. Only then consider promoting any derived finance/valuation pillar into the real six-pillar 100-point score. Until then the current TOP100 display remains transparently based on the existing observed score, not the unreleased V3 composite.

**Version policy:** this extends the SAME evolving screener from v2.2 toward V3. It does not create a competing parallel stock app.
