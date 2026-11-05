# CAPEX original-file sampling recovery

The original-file pilot retained only the newest missing fiscal year for
each issuer before rotating its cohorts. An issuer missing both FY2023 and
FY2024 could therefore never offer FY2023 to the rotation. Independent year
strata did not fix this because the older candidate had already been removed.

The selector now validates every missing issuer/year receipt, keeps all
eligible years and rotates them before selecting distinct issuers. When fewer
missingness classes are populated, unused slots are filled with other distinct
issuers up to the unchanged four-file ceiling. A single issuer is still read
at most once in a run. No provider retry, extra request ceiling or new schedule
is introduced.

Current-model source-work preference is independently reconstructed from the
reconciled R1d parts, comparison and catalog. Issuers with exactly one missing
FCF component are preferred for up to half the explicitly prioritized slots;
the other slots continue general fiscal-year/missingness exploration. This
does not change investment ranking. Known date lag disables the preference;
unreconciled or future priority evidence cannot authorize a provider request.

## November5 saved-source planning audit

| Measure | Count |
| --- | ---: |
| Missing issuer/year candidates retained by latest-only selection | 878 |
| Eligible missing issuer/year candidates across all years | 1,817 |
| Previously hidden older candidates | 939 |
| R1d single-FCF-gap source-work targets | 84 |
| Fixed cohort20 planned distinct issuers | 4 |
| Planned targets from single-FCF-gap group | 2 |
| Planned FY2023 / FY2024 / FY2025 files | 2 / 2 / 0 |
| Dry-run provider calls | 0 |

These are candidate counts, not newly collected files, recovered CAPEX values
or completed score weights. The pilot remains aggregate-only. Tag names,
annual KRW candidates, dimensional hints, presentation roles and calculation
links do not certify a total, statement scope or an amount usable for FCF.
Existing annual receipt, ZIP/XML safety and no-amount/no-score gates remain.
Only anonymized report counts are retained as a14-day Actions artifact; source
archives, issuer IDs, receipt IDs and fact amounts are not published by it.

Six new regressions cover older-year reachability, bounded slot utilization,
general exploration alongside priority, receipt mismatch rejection, independent
priority reconciliation and anonymous live-probe output. Existing original
XBRL structural regressions also remain required.
