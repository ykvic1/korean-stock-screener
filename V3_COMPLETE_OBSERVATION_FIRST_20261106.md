# Complete observation first

The priority is actual100-point component-weight observation, not a score
of100. Missing facts are neither zeros nor grounds for rescaling the observed
fraction. At the November5 baseline,556 of2,501 issuers have all component
weight observed;195 have exactly one incomplete component:

| Remaining component | Issuers |
| --- | ---: |
| Financial cash flow / FCF persistence | 84 |
| PER / reported EPS quality and growth | 71 |
| Correction volume contraction | 26 |
| Four-year revenue growth | 14 |

## First actionable missing source

Among71 PER/EPS cases,28 already have receipt-matched complete reported EPS
but lack the earnings-yield numerator.25 have already been queried under the
same annual receipt and are retained as missing-standard-account evidence.
Three were excluded by the older frozen acquisition cohort and remain
unqueried:060280,126880 and270660. They are source-work targets, not stock
recommendations.

`v3_owner_profit_completion.py` independently reconciles the current R1d
parts/comparison/catalog, validates complete EPS and exact annual receipt/scope,
and appends only these missing-source candidates to the existing source queue.
All prior acquisition members and validated records remain unchanged.
Source headers are reanchored against that expanded immutable cohort before
new requests; financial values, account IDs and observation dates are not
rewritten. The legacy `95weight` field names remain compatibility names for
an acquisition cohort, not counts of currently95-weight issuers.

The existing20-issuer source limit, market interleaving, exact IFRS account
IDs, currency/receipt validation and source-only checkpoint format remain.
Eligible one-gap targets are requested before other pending issuers; already
queried exact receipts are never polled again. A source-verified zero or loss
is an observation, while missing accounts remain null. No EPS-times-shares
inference or looser account-name match is introduced.

After acquisition, the existing source-gated scorecard and operational
publishers recalculate normally. Verified facts can increase full observation
even if their earned points are low. They do not authorize certified returns,
MAE or stop probabilities. If the provider supplies no supported account,
coverage must remain incomplete.

The paired priority/source checkpoint is published together. Post-rebase
refresh rechecks exact identities and receipts without provider calls, and
strict verification precedes push. All previous score-history versions stay.

Six new regressions include an explicitly synthetic full-publication test:
when all three source responses are verified, full observation can rise by3;
that test is not evidence that the live provider has supplied those facts.
