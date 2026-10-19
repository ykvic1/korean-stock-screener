# V3 user-first OBV and downside watchlist (2026-10-19)

## Observed operating baseline

- Price-scored KOSPI+KOSDAQ listed names: **2,368 / 2,497** as of 2026-10-19.
- Existing strict deep-research dossiers: **2**, and confirmed buys **0**.
- Official institutional / foreign buyer flows and verified official PBR: **0 observed**, not zero actual holdings or zero company valuation.
- The existing score-first V3 view defaults to the 2 selected dossiers, leaving the rest of the scored universe hard to use.

## Small practical user-release scope

Make the first tab **"우선 관찰(OBV·패턴)"**. This is a **technical watchlist**, not extra algorithm-certified investment picks. The tab evaluates the **same observed v2.2 daily score snapshot** (no new remote source or future data) and includes a currently priced issuer only when all are true:

- `obv_rising === true` actually observed, v2.2 data coverage >=55%, existing normalized v2.2 score >=60.
- structurally calculated stop distance **3%–15%**; observed stop-price and reported percentage are coherent; no high-risk or liquidity/rapid rally warning.
- available 20-day gain <=15%, 60-day gain <=25% and not less than -35% (avoid chasing or unconstrained falling knives).
- at least one price-unreflected OBV heuristic `divergence===true` with 60-day gain -12% through +10%, one supported preferred `patterns` description (wedge/IHS/triangle/consolidation), or a named early/base/reversal phase.
- Name/code, observed score and risk distance are sorted using **OBV price-lag first**, then preferred pattern, then observed score and coverage; do not award invented score points.

An independent tab **"OBV 상승·주가 미반영"** narrows down to the true price-lag heuristic only. Existing tabs **"심층 선별 후보"**, history-quant, early and all market scores are preserved, as is detail view with structural invalidation, 1/3/6m past quant stats (when observed), OpenDART filing source/PB references and missing factors.

## Strict evidence contract

- OBV rising is an OHLCV-derived price-volume signal. It **does not identify institution/foreign/pension accumulating shares**. The `divergence` signal is a 60-day range **heuristic**, not a statistically established divergence or guarantee of upside.
- Exact v2.2 score, 6-factor v3 null score, existing shortlist count, historical quant, PBR and buy decisions are unchanged.
- No external buy link, orders, commission account or new credentials. Candidate lists can be empty and are never padded to 10 or 20 fabricated investments.
- Regressions explicitly reject missing OBV, invalid stops, weak data coverage, excessive recent appreciation, thin liquidity and absent pattern/phase; guarantee the filtering action cannot certify a purchase.

## Remaining blockers for a production-grade personal portfolio manager

Official 20/60/120/250-day owner flow and regulatory terms, regular-session verified KRX OHLCV volume, revised annual filings with point-in-time provenance, historical delisted constituents, forward walk-forward quality and actual sector membership. No future return projection or verified risk/reward inferred from this patch.

## Validation

Existing end-to-end PR CI and JavaScript/HTML regressions must be green before merge. After merge, verify GitHub Pages deploy and open `https://ykvic1.github.io/korean-stock-screener/v3-beta.html` on mobile. If live current data legitimately produce zero watch rows, show an honest empty state rather than fake picks.
