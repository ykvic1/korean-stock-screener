# Collect parent equity across all coverage bands

The saved 2,501-issuer gap census contains 104 missing PBR factors. Thirty-three
issuers have the required FY2025 consolidated annual receipt, positive total
equity and independently receipt-matched CFO anchor. The previous collector
selected only issuers missing PBR alone and capped accumulated source proofs
at a single small batch.

Select the full eligible cohort, prioritize high coverage, and collect twelve
issuers per pass using four readers and the existing single-checkpoint writer.
The accumulated-proof limit is separate from the per-pass source-call limit.
Admit actual standard reported parent equity directly, or total equity less
EXPLICIT reported NCI. An absent NCI is never zero; direct-owner proofs carry
NCI=null with a distinct contract.

Store receipt/anchor-bound source reviews so unchanged unsupported statements
are not repeatedly fetched. Provider/auth failures interrupt instead of becoming missing-fact observations.
A successful but conflicting issuer response is quarantined with no numeric
evidence; it does not block other issuers and can be retried on the next market
day or a changed anchor. Completed proofs publish before any
interruption is surfaced. Continue only after actual source progress, stop
at an empty queue, and replay valuation only when evidence was added.

No score budget or formula changes. The existing audited book overlay and
current score publisher handle the actual values. The nightly backup follows
market close; a new confirmed market snapshot also checks for new eligibility.
The read-only seven-issuer pilot remains available manually and no longer
makes duplicate provider calls on collector code pushes.

Focused checks cover direct vs explicit-NCI proof contracts, more than seven
accumulated proofs, missing evidence, changed receipt/CFO anchors, source-error
reviews, partial-coverage targeting and bounded continuation. Actual newly
observed values and coverage changes are measured after live collection.
