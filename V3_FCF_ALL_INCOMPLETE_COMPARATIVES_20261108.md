# All-incomplete original FCF comparative acquisition

The November 6 saved-source operational snapshot contains 976 fully observed
issuers out of 2,501 under the 25/30/12.5/12.5/20 allocation. The legacy
20/25/20/20/15 observation report contains 1,050; the two contracts must not
be presented as the same coverage measure. Neither count is a score of 100.

After PR #636, the original FY2025 comparative collector has exhausted its
one-missing-component cohort. Its selection rule excludes multiple-gap
issuers even when their prior-year CFO, scope and annual receipts are present.
On main df6de9401030c90d8315f4dc36a7b7e5b133819f, admitting the existing
multiple-gap FCF queue yields 543 additional unreviewed source identities.
This is an acquisition cohort, not a forecast of recoverable issuers.

Selection now processes one-gap candidates first, followed by the existing
multiple-gap priority index whose exact shape contains the financial FCF
persistence component. Other gap shapes are excluded. Duplicate codes are
visited once. Same-receipt and same-anchor research markers prevent repeated
requests; a revised receipt or anchor permits an independent new review.
All existing issuer, scope, original annual receipt and integer CFO gates
remain. Batches retain the 16-issuer ceiling, six-minute collection budget,
per-issuer checkpoint and stop-on-provider-error behavior.

Successful main research completions now trigger the existing promotion
workflow. It checks out main, verifies cross-receipt proof, serializes with
other cashflow writers, and dispatches the existing observation replay only
after changed verified source observations are saved. Failed research and
pull-request workflow runs cannot trigger source promotion. No workflow
artifacts or source-run code are consumed.

Regression tests cover one-gap priority, multiple-gap admission, unrelated
shape exclusion, duplicate prevention, unchanged-marker reuse, receipt
revision, scope rejection and malformed shape indices. The existing FCF
promotion and cashflow tests retain explicit-zero versus missing handling,
conflict rejection and revision rollback. No published source amounts or
scores are edited by this patch; live results must be checked after runs.
