# Initial data acquisition: immediate concurrent burst

Initial acquisition must run during active development, without waiting for a
scheduled market close. The FY2024 original-FCF path now supports four parallel
source readers and six consecutive batches of sixteen issuers (maximum 96 per
invocation). A shared twelve-minute budget stops new batches early enough for
validated checkpoints to publish. Each batch also keeps its six-minute cap.
Existing receipt/scope/both-CFO and explicit-CAPEX checks remain unchanged.

HTTP sessions belong to individual threads. A single controlling thread checks
and writes every completed research record; workers never mutate shared JSON.
Only four issuer tasks can be in flight, with no large pre-submitted queue.
After a source failure, no additional issuers start; valid already-in-flight
responses are drained and checkpointed before surfacing the error. Same-source
checkpoint markers prevent repeat requests across batches and resumed runs.
An invocation is bounded by 192 profile/archive requests plus one optional
company map request. The existing single bounded retry can resume saved
progress; provider quota/auth/unknown errors are never missing-account markers.

The reviewed main merge starts this acquisition immediately, then successful
saved progress continues the finite queue. The prior sixteen-issuer FY2025
archive route keeps running independently. Raw source promotion and score
publication retain their existing serialized writers and proof checks.

The full-market update owner remains weekdays at 18:30 KST, as configured in
full-market-batches.yml. This supplemental source workflow has a weekday
18:40 KST backup and also follows successful main cashflow-source runs. If
the current market and major-account source dates are not aligned, it preserves
prior evidence and returns without provider calls; completion of the source
pipeline resumes it. Once initial gaps are exhausted, unchanged receipts make
collection a no-op, while newly revised source identities are reviewed again.

No source facts, observed weights or investment-grade certification are
manufactured. Regressions prove actual simultaneous readers with a barrier,
one checkpoint writer, request ceilings, disjoint resumed targets, preservation
of valid in-flight responses on failure, and no calls during source-day lag.
