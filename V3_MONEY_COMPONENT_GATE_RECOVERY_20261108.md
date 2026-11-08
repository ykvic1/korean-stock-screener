# Independent actual-money observations without the longest-window gate

The saved-source operational report has 982 issuers missing the same five
money-structure components. The source exporter first required the entire
legacy participation-six calculation (four daily and five weekly comparisons)
to succeed. A missing 120-week comparison therefore prevented even a valid
20-day/5-week concentration or 60-session asymmetry observation from leaving
the private snapshot. The structure calculator repeated the same all-window
gate. Source acquisition and per-component availability were conflated.

Keep the legacy participation-six contract and its fully observed scores
unchanged. Add a separate source-bound component receipt to the same atomic
report. It uses the existing issuer identity, catalog fingerprint, private
revision, source checkpoint hashes and independently dated daily/weekly ends.
No private amount, ratio, price, path or reconstructable time series is exported.

| Component | Required evidence and availability |
|---|---|
| Persistence, maximum 5 | Each daily comparison retains 0.625 and each weekly comparison 0.5 of fixed available weight. Missing windows remain missing; no renormalization. |
| Concentration, maximum 2 | The 20-day comparison and 5-week comparison independently contribute up to 1 each. |
| Up/down asymmetry, maximum 2 | Twelve valid five-session intrabucket pressure observations; no claim about missing boundary price signs. |
| Correction contraction, maximum 1 | Existing 60/120/240-session up/down sample rule, unchanged. |
| Long weekly increment, maximum 2 | The original 120-week versus previous 120-week comparison and short daily reference are still required. |

Six-point growth retains its original complete-source formula. A short source
cannot replace it with a rescaled short-window score. Fully observed component
formulas remain identical to the existing R1d decomposition. Partial receipts
do not displace an older, separately observed receipt with more available
weight; its actual original date stays visible.

The operational reader validates component evidence independently of whether
the legacy six-point source record exists or legacy OBV inputs are usable.
Observed zero, unavailable input and partial available weight remain distinct.
Missing long periods still prevent full-100 observation: this change does not
invent 240 weeks of history or promise 982 additional fully observed issuers.

Source projection also counts complete histories, missing older tails, internal
holes, missing latest periods and entirely absent histories, as aggregates only.
These patterns do not establish listing dates, trading halts or code changes.
They guide targeted source recovery after the real snapshot is replayed.

Regression coverage checks unchanged full-source formulas, independent short
observations with one older missing week, fixed partial weights, zero versus
missing, source identity/date/provenance rejection, no raw amount export, and
operational consumption without a legacy six-point or OBV qualification. Main
deployment replays already saved private data with zero new KRX provider calls;
actual observation gains and remaining history patterns must be measured then.
