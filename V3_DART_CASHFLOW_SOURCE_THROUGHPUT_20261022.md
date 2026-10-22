# DART annual cashflow/CAPEX: bounded source throughput (2026-10-22)

## Verified baseline, not projected coverage

The published `docs/v3-dart-cashflow-reference.json` for market close
2026-10-21 contains 684 current-listed issuers / 2,052 actual annual
company-years (FY2023–25). Of these, 2,047 report an explicit operating
cashflow, 1,504 have both PPE and intangible CAPEX, and 1,502 have a
computable FCF. The separate source-identity check supports 508 actual
FY2025 FCF issuers and 420 complete three-year FCF histories.

Missingness in those 2,052 company-years (overlapping categories):

| Explicit source component | Unobserved years |
| --- | ---: |
| Operating cashflow | 5 |
| PPE acquisition cashflow | 266 |
| Intangible acquisition cashflow | 495 |
| Either CAPEX component | 548 |
| Calculated FCF | 550 |

None of these missing values is converted to zero. Source account names
without a uniquely verified IFRS taxonomy ID remain out of scope. An
unmatched original annual filing receipt remains untrusted for financial
scoring, even if other numeric rows happen to be present.

## Bounded collection increase

- One run every hour at minute 47 remains unchanged.
- One run now examines **at most 40** qualified current-listed issuers,
  up from 32 (up to 120 annual source calls, versus 96).
- An uncached corporate identification request may add one source call;
  the hard overall call cap is **121**. The 950-second collection time cap
  and per-fiscal-year checkpoint behavior remain intact.
- Official OpenDART normally returns its request-limit error beyond about
  20,000 requests/day, but a key may have a lower individual quota; this
  workflow must not treat 20,000 as a guaranteed entitlement. The new
  cashflow theoretical daily source budget is 2,880 annual calls (24 x
  120), plus at most 24 mapping calls if caches consistently fail. Other
  DART callers share the same key, so actual quota errors stop ingestion.
- Only genuine complete CF rows and identical issuer/year/scope/annual
  filing receipts contribute to FCF. Public results contain computed
  values and fixed provenance fields, not raw provider account rows.
- The workflow's summary prints non-sensitive counts of which CF
  components remain missing, to prioritize subsequent genuine data work.

## Validation boundary

Synthetic no-network test exercises **40 issuers x 3 complete years =
120 source calls**, checks exact no-refetch resumability, enforces the
40-issuer cap, and confirms three-year matched counts and verification.
This does not certify future live API availability, IFRS/XBRL alias
equivalence, as-reported PIT histories, the six-pillar score, or any buy
signal. After deployment, confirm the first real 40-company Actions run
and compare the actual new FCF count; do not present theoretical capacity
as observed improvement.
