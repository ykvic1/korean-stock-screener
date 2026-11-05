# R1d saved-source model diagnostics

This is a separate validation work unit. It changes no score, ranking or
collection budget. `v3_operational_model_diagnostics.py` independently checks
R1d component/coverage reconciliation, common issuer count and top-cohort
composition before producing `docs/v3-operational-model-diagnostics.json`.
It runs with saved-source operational publication and again after rebases.
During a known catalog date rollover, it preserves its previously dated
snapshot instead of joining newer market caps to older scores.

## November 5 baseline

2,373 issuers have both operational and legacy ranks in the same saved
snapshot. Compare these same issuers on both sides; do not compare differing
universes or replace missing market capitalization with zero.

| Top cohort | Model | KOSPI | Cap below KRW100bn | Cap KRW500bn–5tn |
| --- | --- | ---: | ---: | ---: |
| 50 | Legacy | 19 | 12 | 13 |
| 50 | R1d | 24 | 11 | 14 |
| 100 | Legacy | 42 | 30 | 25 |
| 100 | R1d | 46 | 27 | 27 |
| 200 | Legacy | 87 | 64 | 39 |
| 200 | R1d | 100 | 55 | 51 |

R1d does not increase the sub-KRW100bn share in this snapshot. However,
67 of its top100 remain below KRW500bn. This does not establish acceptable
volatility, stop probability or investment performance. No hard market-cap
exclusion or penalty increase is introduced.

## Diagnostic scope and limits

- Top50/100/200 entries, exits, market mix and market-cap distributions.
- New-model component means with observed/missing denominators; legacy
  component contributions are unavailable and are not invented.
- A ranking-only counterfactual removing observed financial-risk deductions,
  retaining market-cap and stop-distance adjustments and the producer's
  rounding/tie order. It measures rank impact, not avoided losses.
- Cross-sectional component correlations use only fully observed 100-weight
  issuers, with a minimum30. High correlation is a review flag; low correlation
  does not prove the absence of duplicate rewards or predictive validity.
- Older retained components and unconfirmed financial-risk counts remain
  explicit. Observation completeness is not used as a primary rank sort.
- 1/2/3/6/12-month returns, MAE and stop-first frequency stay null because
  verified point-in-time outcomes have not been supplied to this diagnostic.

The baseline has556 fully observed issuers. Their component pairs show no
absolute Pearson correlation at or above0.85; that is exploratory descriptive
evidence only. Corporate-action, historical membership, corrected-filing
effective dates and price-adjustment release gates remain open.
