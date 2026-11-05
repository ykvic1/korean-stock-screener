# R1d source recovery — 2026-11-06

## Observed baseline

The 2026-11-05 operational R1d snapshot contains 2,501 listed issuers,
2,481 ranked issuers and 556 issuers with all 100 points of component
weight observed. This is evidence availability, not a score of 100 or
certified investment performance. The older near-100 ledger uses older
weights and must not be used as R1d coverage.

## Four-year collector failure

Scheduled run 42082786137 failed on `lookahead_or_pre_yearend_receipt`.
The strict calendar-year validator must continue rejecting such periods.
A receipt before the calendar year end can indicate an unverified
non-calendar fiscal period; it is not automatically a fraudulent filing.

The collector now isolates only these temporal period errors, keeps
other usable years and issuers, and retains previously validated cached
years. It records review reasons without publishing the rejected values,
does not count rejected periods as fresh evidence, and resets batch-health
expansion. Identity, malformed values and other schema failures remain
blocking. Public `merge` and `_record` remain strict.

## Current-model completion queue

`v3_operational_evidence_priority.py` independently reconciles the R1d
component weights, public counts and listed catalog before producing
`docs/v3-operational-evidence-priority.json`. It makes no provider calls
and adds no points. Publication recomputes it alongside operational
scores, including after publication rebases.

At this baseline, 195 issuers have exactly one incomplete component:

| Component | Issuers |
| --- | ---: |
| Financial cash flow / FCF persistence | 84 |
| PER / reported EPS quality and growth | 71 |
| Volume contraction during correction | 26 |
| Revenue growth | 14 |

These are investigation candidates, not guaranteed recoverable points.
The remaining 1,750 incomplete issuers retain a compact missing-component
index. Catalog-confirmed short history is distinguished from price-source
errors and unknown window gaps. Previously queried missing CAPEX is
marked for original-filing review, not repeated blind account requests.
Source-work priority does not change investment ranking.

## Remaining release conditions

Historical point-in-time disclosures, independent corporate-action and
adjusted-price checks, historical membership including delistings, and
forward outcome/MAE/stop-first validation remain separate release gates.
No certification or fabricated completeness is introduced by this patch.
