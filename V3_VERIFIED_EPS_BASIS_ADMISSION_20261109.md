# Research score admission for verified annual EPS

Use a separate derived adapter for the prior-filing audit. Keep the original
single-filing EPS source and its proof unchanged. An audit flag alone is not
sufficient: verify its full digest/structure, current market epoch, issuer,
annual receipts, current baseline EPS, research candidate, cached CFO anchors,
reader fingerprint and saved annual-book hash before admitting an issuer.

Only actual reported three-year EPS reach the existing growth/stability part
of PER/EPS. Its available weight remains 10/3 points; earning yield still needs
its own evidence. No full score is manufactured, budgets remain unchanged,
missing values stay missing and existing complete single-filing EPS wins.

The operational workflow follows successful trusted-main audits, reads only
saved sources and publishes through the existing score-preservation path.
Hashes include the audit, research and cashflow inputs plus admission readers.
The report exposes admitted issuer counts; corporate-action and performance
certification flags remain false.

Validation includes proof/receipt/issuer tampering, stale audit epochs, changed
annual books, genuine zero EPS, blocked results and unchanged unrelated score
parts. Six focused admission tests and thirteen operational tests pass locally.
