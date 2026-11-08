# Remove completion-only gating from original owner-profit acquisition

The API supplementary queue already covered all listed missing owner profit,
but original-XBRL recovery iterated only `preferred` one-gap completion cases.
Other missing fields therefore prevented independent profit acquisition. At
this checkpoint the old route selected 1 issuer while 134 missing-profit
issuers had an eligible exact-receipt/scope cashflow anchor or could request
one from the matching official annual CF statement. 21 need that independent
CF request. No provider call is made by the dry-run plan.

Acquire all these unreviewed originals, with completion cases first and flat
source quarantines last within each priority tier. Keep exact receipt, scope,
entity, currency, annual period, CFO and attributable-profit verification.
Do not infer NCI or profit as zero, reuse a different scope, replace existing
observations, weaken archive checks, or retry already reviewed absent facts.
The original source/proof contract and scoring formulas are unchanged.

Keep finite four-by-eight checkpointed batches and publish before surfacing a
source interruption. Continue immediately only after a non-interrupted run
that actually examined at least one issuer and still has unreviewed eligible
originals. This prevents a missing company mapping or a zero-progress run from
creating an endless dispatch loop. A weekday 20:03 KST backup covers later
eligible source additions; an exhausted queue makes zero provider requests.
Both original and API profit writers retain pending work under the existing
shared lock with `queue: max`.

Validation covers multiple-gap acquisition, completion priority, explicit
zero/loss preservation, prior negative reviews, exact scope/receipt/CFO gates,
independent-CFO fallback, identity mismatch, unchanged original proof admission,
and progress/interruption guards for continuation.
