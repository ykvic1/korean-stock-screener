# Targeted private weekly amount recovery

The deployed component split exposed 508 issuers with internal weekly holes.
This is a coverage shape, not proof of a halt, listing date, or source defect.
The original collector gated reported trading value on complete OHLC fields.

This repair selects actual missing weeks between observed weeks, prioritizing
issuers with exactly one absent week across the entire 240-week chain. The
first bounded live run examines at most three weeks / thirty KRX market-day
requests. Missing edge history is not reclassified as an internal hole.

For every selected week:

- Validate official response identity, dates and complete trading sessions.
- Independently read explicit unsigned `ACC_TRDVAL`, including a reported zero.
  A missing field, dash, fractional amount, or absent issuer stays unavailable.
- Require the fresh amount totals of **all established issuer-week factors**
  to match their original values. A source revision blocks this repair.
- Save only missing issuer-week amount aggregates in an immutable private
  sidecar pinned to the exact original archive digest. Never overwrite the
  original archive or manufacture OHLC, price direction, or halt evidence.
- Checkpoint successful and unavailable weeks; repeated runs do not blindly
  request the same unsuccessful windows. Recover orphaned receipts without
  querying KRX again. The shared private-money writer lock remains in place.

The saved-source scoring workflow reads the index and sidecars from the same
pinned private checkout as the original chain, validates every receipt and
adds only missing factors. Publication exposes aggregate counts, source dates
and bounded score receipts, never raw daily amounts or private issuer series.
Existing six-point formulas, long-period requirements and certification flags
remain unchanged. A repaired week does not automatically mean a complete
100-point issuer: daily money, other weekly gaps, EPS and FCF can still be absent.

Validation covers explicit-zero versus missing, invalid OHLC isolation,
source-revision rejection, cross-date rejection, request budgets, immutable
archives, digest/flag tampering, orphan resume, idempotency, single-hole priority,
and end-to-end saved-source replay into full long-window observations.
