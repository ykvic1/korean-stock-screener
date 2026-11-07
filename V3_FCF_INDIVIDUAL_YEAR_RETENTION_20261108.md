# Retain each verified FCF year independently

The expanded all-incomplete collector is acquiring original FY2025 comparative
evidence in bounded batches. Its first 16-issuer batch completed successfully,
with two complete-year CAPEX candidates. Source observations and operational
coverage are replayed by the existing chained workflows.

An additional promotion restriction discarded a verified complete year whenever
another missing year for that issuer remained incomplete. This patch retains
each independently complete prior-year CAPEX pair, while keeping the remaining
year unavailable. It never promotes a partial CAPEX pair or treats an absent
component as zero. Three-year FCF availability still requires all three years.

On source snapshot d3c3d89072b54123a090c71f318875f97bbb15d0, offline replay
recovers five verified company-years (001560, 095190, 206650, 294570, 307870).
Observed FCF company-years increase 5,498 to 5,503; three-year-complete issuers
remain 1,585. These are verified offline results, not new full-100 observations
or a claim about a later live snapshot.

To avoid downloading the same archive again after an individual-year repair,
research-marker reuse reconstructs the original anchor fingerprint only from
verified additions bound to that exact research record. A changed receipt,
scope, CFO, existing component or newly missing year invalidates reuse. A
tampered promoted value fails proof verification. Repeating promotion is
idempotent. Existing later-filing revision rollback remains active.

Promotion diagnostics now distinguish issuer repairs from new three-year FCF
completions, rather than calling every repair a full issuer completion. No
source files or investment scores are edited by the patch itself. Tests cover
mixed complete/incomplete years, unchanged-marker reuse, source changes,
idempotency, absence versus zero and tampered provenance; the actual saved
source replay passes cashflow state verification without provider requests.
