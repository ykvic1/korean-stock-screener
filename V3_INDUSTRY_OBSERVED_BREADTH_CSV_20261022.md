# V3 current-company DART industry operating-profit breadth — observed CSV

2026-10-22. This feature makes **existing actual-source DART 2024/2025 profit observations** accessible in the unified V3 screen. It adds **zero API calls**, does not alter the main weekday 18:30 market scan or certify any part of the industry 15 points.

## Evidence before this change

- Current-listed catalog: 2,497 companies, all with saved current-company DART `induty_code`.
- Verified FY2024–FY2025 operating-income comparisons (including 79 without valid revenue account): **2,355/2,497**, observed coverage **94.3%**.
- Strict revenue-included comparisons stay **2,276**, with 221 not strictly comparable. 79 profit-only firms must **not** be reclassified as having verified revenue.
- Two-character DART current-industry prefix × KOSPI/KOSDAQ yields **105 groups**, including groups too small for a public ratio.
- Official KRX sector historical membership, adjusted-price sector returns, PIT identities, official current 15/100 industry score: **not verified**.

## Feature

Existing `v3_industry_earnings_gap_audit.py` already validates the same-market-date catalog, DART current-code set and annual filing histories and reconciles individual FY2024/25 data with the independent industry sample. In the **same validated and commit-rebased workflow**, export:

`docs/v3-industry-observed-profit-breadth.csv`

For each code prefix/market group, include:
- current classified issuer count;
- exact operating-profit FY2024/2025 matched issuer count, and stricter revenue-included matched count;
- when at least **3** independently verified profit pairs exist, actual improving issuer count, observed sample improving ratio;
- worst/best-case improvement ratio over the entire **current-code group**, treating missing businesses as all non-improving/all improving; and
- actual profit-pair coverage and source market day.

The lower/upper limits are **missing-data arithmetic bounds**, not confidence intervals or buy/sell forecast scenarios. Groups of 1–2 comparable issuers hide improvement numerators, percentages and bounds. All rows show `과거KRX업종구성_PIT=미확인`; every `공식산업15점` is blank (not zero).

- UTF-8 BOM and CSV quoting for Excel. No FY operating-profit values, financial statement rows, filing receipts, 8-digit DART IDs or per-issuer profit direction published.
- Page `index.html` and V3 `workspace.html` link this actual-current-code source CSV beside the existing missing-source issuer worklist.
- Existing source-joined 2-hour industry workflow publishes all three outputs together, recomputes after any successful rebase, and verifies rendered CSV before a non-force push.
- A source mismatch, inaccurate grouped total, forged 15-point certification or modified downloaded CSV fails closed.
- No raw KRX OHLCV, official IAS identities, or extra DART calls.

## Follow-up

Keep the official 15-point industry dimension unverified until historical KRX membership/PIT and sufficiently synchronized sector price and earnings cycle are independently validated. The new observed data allows human comparison of **current-company historical earnings improvement** without fabricating sector rotation, institutional positioning or a verified investment grade.
