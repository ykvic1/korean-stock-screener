# KRX private 5-session boundary + research return catch-up (2026-10-22)

## Actually observed source before the change

The 2026-10-22 14:35 KST historical derived-only source run
(`39728875264`) successfully persisted **105** five-session cohorts,
**525** historical trading-session candidates and **219,764**
private derived issuer-cohort factor records. It made **160** official
KRX market calls in that particular source run; it saved **zero raw
OHLCV files**. Of 490 five-session cohorts, 385 remain uncollected.

The independently observed prior cross-cohort boundary checkpoint was
24 links. The sources and bridges are genuinely separate: a derived
5-session cohort **does not contain** the close-to-close return across
two cohorts; computing each missing boundary requires 4 additional
official KRX calls (2 adjacent trading days x 2 markets).

## Bounded catch-up change

| Pipeline stage | Before | After | New official KRX calls |
| --- | ---: | ---: | ---: |
| Private five-session source / run | 16 cohorts | unchanged 16 | unchanged 160 max |
| Private derived boundary / run | 16 links | **24 links** | max **96** instead of 64 |
| Derived research returns / horizon / run | 16 windows | **24 windows** | **0** (unchanged) |
| Private source quality review / run | 16 cohorts | unchanged 16 | **0** (unchanged) |

The existing three daily KRX pipeline schedules and v2.2 weekday
18:30 KST production run remain unchanged. At all three scheduled
invocations, the incremental bridge-call bound is 96 additional
requests per day versus the previous implementation (maximum 288
bridge KRX calls/day, rather than 192). The combined theoretical
five-session source + boundary calls are bounded by 768/day, before
other independent KRX operations, when all three runs fill their
budgets. This is a *theoretical cap*, not observed daily consumption
or a guarantee of source uptime.

Only a verified private immutable 5-session artifact permits a bridge
read. Links are derived immediately in memory and stored only as
rounded issuer logreturns in the private archive; raw official prices,
volume, order book and provider rows cannot enter public pages or
research scores. Boundary checkpoints retain SHA readbacks, fail
closed on missing/corrupt inputs and resume without repeating a
verified existing link.

Rolling returns are approximate, unadjusted derived research signals;
corporate actions, PIT reconstruction, exact adjusted returns and
issuer continuity are **not certified**. They do not promote any
official 100-point score, investment recommendation or IAS20 evidence.

## Offline regression acceptance

- 26 source cohorts produce 24 links / 96 official calls on first run;
  next run creates the 25th link with 4 additional calls, no re-fetch.
- 33 source cohorts produce 24 then 6 new 20-session return windows,
  with zero additional KRX requests and exact private checkpoint resume.
- A 25-link or 25-window per-run request fails at the configured cap.
- Retain tests against raw OHLCV persistence, invalid SHA, source
  mismatch, missing link, zero-fill and public-target leakage.

After merging, inspect the *next actual* scheduled boundary and rolling
Actions logs before reporting any higher real coverage count.
