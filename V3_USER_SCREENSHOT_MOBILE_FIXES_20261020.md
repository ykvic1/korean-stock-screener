# V3 mobile convergence update — collapsed notes, global ticker lookup and six-domain evidence

2026-10-20 KST: user submitted screenshots of the actual Android unified V3 screen and flagged three real usability defects.

## Confirmed GitHub/PC continuity

- PR #132 was already merged to main at `6c146bee4e504defbaea3209618b9509b692857c`; neon-green score ranks / seven-column strip seen in the screenshot are indeed from this version.
- PR #133 (`v3-fouryear-dart-financial-reference-20261020`) is still OPEN, with its own successful CI. It does not yet constitute a deployed, source-verified full finance pillar.
- The user screenshot shows the old static docs/data.json snapshot; empty V3 factor cells are unverified sources, not missing CSS. **Do not substitute 2.2 raw subcomponents for IAS20, finance15, valuation15, industry15, momentum10.**

## Actual improvements (same unified app, no new separate UI)

1. "오늘의 시장 관찰": native accessible `details/summary`, **initially closed**, one-tap open/close, source-date status always visible.
2. Replaces the oversized filter/settings card with **one prominent ticker search** and short TOP100 + category shortcuts. Market/sort/strict downside controls are under optional `details` with no sticky huge introduction.
3. Search is truly global. A nonempty query matches name/6-digit code/sector across **all** screened stocks *regardless of* top100, selected RSI/quant tab, KOSPI/KOSDAQ or safe-only state. Stock cards always reveal legacy global score rank and downside warnings; browsing without query still respects all filters.
4. Cards show the approved six weighted pillars **IAS20 / technical25 / finance15 / value15 / sector15 / momentum10** with `미검증` for source-missing factors, numeric 25-point technical evidence only if the live source-date check passes and no fake V3 investable score. V3 total remains `미산출`; old v2 observation score is separately labeled and not used as the new full six-domain score.
5. Updates both canonical root index and secondary workspace HTML identically. Preserves original `legacy.html`, source pipeline, official DART API safeguards, quant and Excel, GitHub Pages paths.

## Verification and deployment gate

- Offline Node regressions check native collapsed details, optional advanced controls, first search, six-pillar labels/weight100, low-rank #106 found with TOP100 on and wrong tab, true global legacy rank and saved real market compatibility.
- **Chromium 390x844 mobile** opens/closes source summary and advanced settings, searches an actual last-listed issuer with TOP100 + RSI tab active, checks all six maxima, details and Excel download and no page errors or horizontal overflow.
- No external provider calls or user data storage changes. CI and real Chromium must both pass at the PR's latest head before merge. Do not merge PC PR #133 or infer current full-finance score based on it just for UI.

This is a corrective **UX + behavior** PR; true 6-domain scored ranking still depends on official provenance, as-of financial PIT series, investor flow, sector cohorts and out-of-sample validation.
