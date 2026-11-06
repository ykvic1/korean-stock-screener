# Complete the missing balance fields in reused annual-book years

A saved fiscal-year key does not establish that every account was queried.
The bulk collector skipped every existing year, including FY2025 rows copied
from the annual-book collector. These seeds intentionally have no assets;
thus they cannot establish liabilities/assets or three-year FCF/assets even
when their other accounts and cash flows are already observed.

The November5 archive contains543 such seed issuer/years (442 CFS,101 OFS).
Of the1419 issuers with three receipt-matched annual FCF values,302 lack only
the FY2025 asset denominator; another issuer lacks FY2024 assets from a real
API response and is outside this narrowly defined recovery path.

`--recover-seed-balance` explicitly admits unqueried seed balance gaps into
the existing all-market cohorts. The default collection contract stays
unchanged. Cohorts still have at most100 names, fiscal requests at most25,
three cohorts per run, at most16 fiscal calls and the same elapsed budget.
Only the missing fiscal year is requested; saved FY2022-24 observations are
not recollected.543 targets need two bounded runs, normally12+10 fiscal calls.

An enrichment accepts only a normalized explicit-KRW balance sheet with the
same issuer, fiscal year, CFS/OFS scope and filing receipt as the seed. All
three balance accounts must be integral, unambiguous and satisfy the existing
accounting-identity tolerance. Every already known balance value must agree
exactly. Only missing BS values are filled; prior income, known balance
amounts, other scopes and historical fiscal years remain unchanged.

Empty/missing/ambiguous accounts, changed receipts and conflicting balances
retain the seed unchanged and record a finite checked-but-unavailable result.
The checked receipt prevents blind retries; a changed seed receipt can become
eligible again. Non-seed source years retain the overwrite prohibition.
Quota, malformed responses and unknown currency still stop the source runner
with previously verified checkpoints preserved.

Seven new regressions cover opt-in admission, single-year-only requests,
preservation of existing values, no repeat requests, empty/foreign/revised/
conflicting results, fixed budgets, strict overwrite/marker guards, observed zero assets versus an undefined
ratio, and the
whole real saved census. A deliberately synthetic same-receipt integration
replays restored asset denominators through financial/FCF source checks,
issuer ledger, operational score and CSV verification. It proves plumbing,
not that the provider has already supplied any live asset value.

Weights, score formulas, industry exclusion, missing-not-zero rules and
uncertified research labels are unchanged. Actual recovered observations and
full-100 counts must be verified after live collection and publication.
