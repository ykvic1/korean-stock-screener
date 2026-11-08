# Verify EPS comparability before using prior-filing candidates

The initial queues are complete: 205 current-original source reviews added
45 EPS year values for 23 issuers, while prior-original research has 13 FY2023
candidates. Those candidates are not yet a scored three-year series.

This audit first checks the latest FY2025 API receipt and operating cashflow,
official issuer profile, and the actual FY2025 original. A newly readable
complete three-year EPS series in that one filing is identified separately;
no old FY2023 value is spliced into it.

Otherwise require latest same-scope FY2023/FY2024/FY2025 API receipt identities,
the original FY2024 and FY2025 EPS overlap, and matching common-year cashflow
comparatives. Positive exact-decimal FY2024 weighted-average ORDINARY shares
must agree across both originals. Equal zero EPS cannot substitute for this
share-basis check. Spot shares, diluted shares, preference/subclass dimensions,
currency-only units, missing counts and approximate ratios cannot qualify.

The proposed FY2023 EPS must independently match the latest FY2023 API basic
EPS, or its latest original when the API omits it. Actual reported values are
compared; EPS is never calculated from profit or inferred from missing facts.
Revised/unmatched latest receipts and conflicting financials stop qualification.

Four bounded readers use the shared single-checkpoint writer. Provider errors
do not create missing-fact reviews. Valid in-flight audits publish before an
interruption is surfaced. Fresh daily/market-epoch receipt checks can reuse
verified originals only when input identities and cashflows are unchanged.
Unsupported original facts are not blindly re-downloaded each day; receipt,
baseline, research or reader changes make them eligible for a new check.

Only the audit JSON is written. Scored EPS, formulas and operational scores
remain unchanged at this stage. Audit results must pass a separate explicit
source-admission adapter before scoring; corporate-action certification remains
false. The next stage depends on measured real source results, not forecasts.

Primary accounting references:
- https://www.ifrs.org/content/dam/ifrs/publications/pdf-standards/english/2024/issued/part-a/ias-33-earnings-per-share.pdf
- https://www.ifrs.org/content/dam/ifrs/standards/taxonomy/general-resources/formula-documentation.pdf
