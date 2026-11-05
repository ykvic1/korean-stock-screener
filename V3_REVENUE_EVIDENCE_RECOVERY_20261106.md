# Independent revenue evidence recovery

R1d inherited a dependency on four years of operating income when importing
the four-year revenue CAGR. Four positive reported annual revenues are enough
to compute that metric; missing operating profit must remain a separate gap.

The operational producer now independently verifies four annual receipts,
one accounting scope and four positive integer KRW revenue observations.
It retains the established CFS/OFS choice when the existing complete-income
window is usable. It neither combines scopes nor substitutes a shorter
window, inferred revenue, a zero or a later receipt for a missing year.

The three-year CAGR formula, same-market percentile, minimum30 peers,
source 4-point rounding and conversion to the existing R1d3.5-point budget
are unchanged. The peer cohort includes all newly usable observations, so
existing peers can also move slightly. Operating margins, cash flow and
financial-risk assessment remain independent. The legacy model and the
original financial archive are unchanged.

## Saved November5 baseline

| Measure | Before | After |
| --- | ---: | ---: |
| Issuers with observed four-year revenue CAGR | 2,002 | 2,005 |
| KOSPI revenue peers | 704 | 706 |
| KOSDAQ revenue peers | 1,298 | 1,299 |
| Full100-weight observed issuers | 556 | 556 |
| Added provider requests | 0 | 0 |

| Issuer | Previous available weight | Corrected available weight |
| --- | ---: | ---: |
| NI스틸 | 88.833333 | 92.333333 |
| 멀티캠퍼스 | 90.5 | 94 |
| 두산퓨얼셀 | 90.5 | 94 |

These observations are not investment recommendations or measured returns.
The existing four-year complete-income count is not promoted: the missing
profit observations remain missing. The14 near-complete issuers missing
their first revenue year remain unfilled; a three-year data series is not
silently substituted for this metric's four-year source requirement.

Five new regressions cover independent revenue, missing/zero/future-period
guards, scope preservation, peer minimum and direct operational integration.
New module and source-helper code hashes are included in the operational
implementation digest; immutable score-history snapshots distinguish this
calculation fix without losing prior R1d observations. The publisher rebuilds
scores, CSV, availability priority and diagnostics using saved sources only.
