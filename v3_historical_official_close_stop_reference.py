"""Distinguish an official KRX *closing* support breach from intraday touch.

Replay the authenticated 245-day KRX CLOSE observations in chronological order
through frozen original/successor holder units. Confirm no KRX CLOSE breach
precedes the previously replayed public-OHLC first LOW touch. A later (or
missing) closing breach is **not** evidence that an intraday LOW did not touch.
Missing and suspended prices are NEVER carried forward; the actual price
of a share-exchange entitlement before new stock can trade is unknown.

Sensitive issuer identities, prices and breach dates are used in memory only.
No new KRX provider calls or private archive reads.
"""
from collections import Counter
from decimal import Decimal

from v3_historical_market_coverage import day
from v3_historical_official_holder_terminal import _amount, require
from v3_historical_official_holder_daily_path import valid_summary as valid_holder
from v3_historical_official_holder_gap_ledger import valid_summary as valid_gaps

SCHEMA = "v3-historical-official-close-structural-reference-v1"
OUTCOMES = (
    "public_first_low_and_official_close_same_day",
    "official_close_breach_later_than_public_first_low",
    "public_first_low_but_no_official_close_breach",
    "no_public_low_touch_and_no_official_close_breach",
    "official_close_breach_before_public_first_low",
    "official_close_breach_without_public_low_touch",
    "public_first_low_missing_official_traded_close",
)
BAD = frozenset(OUTCOMES[-3:])
HORIZON_OUTCOMES = (
    "low_touch_with_official_closing_breach_by_horizon",
    "low_touch_without_closing_breach_by_horizon",
    "no_low_touch_and_no_official_closing_breach_by_horizon",
    "closing_breach_precedes_reported_low_touch",
    "closing_breach_when_no_reported_low_touch",
    "reported_low_touch_outside_traded_holder_path",
)
HORIZON_BAD = frozenset(HORIZON_OUTCOMES[-3:])
EXACT = "official_close_and_holder_units_match"
UNTRADED = "untraded_in_both"


def _first_close_breach(dates, positions, reference_line):
    return next(
        (session for session in dates if positions[session][0] == EXACT
         and positions[session][1] <= reference_line), None)


def summarize(plan, observations, authenticated_daily, authenticated_gaps):
    """Publish only anonymous 17-origin + 85-horizon breach comparisons."""
    require(type(plan) is dict and type(observations) is list
            and len(plan["reference"]) == 17 and len(plan["horizon"]) == 85
            and len(plan["days"]) == 244
            and valid_holder(authenticated_daily) and valid_gaps(authenticated_gaps),
            "official_close_stop_evidence_prerequisites_missing")
    expected = {(origin, session) for session, targets in plan["by_day"].items()
                for origin, symbol, units in targets}
    require(len(expected) == 3461 and len(observations) == len(expected),
            "official_close_stop_observation_roster_changed")
    seen = {}
    for origin, session, status, value in observations:
        require((origin, session) in expected and (origin, session) not in seen
                and status in (EXACT, UNTRADED)
                and ((status == EXACT and type(value) is Decimal
                      and value.is_finite() and value > 0)
                     or (status == UNTRADED and value is None)),
                "invalid_official_close_reference_observation")
        seen[origin, session] = (status, value)
    require(set(seen) == expected,
            "unmatched_original_successor_stop_quote")

    outcome = Counter(dict.fromkeys(OUTCOMES, 0))
    horizon_outcome = Counter(dict.fromkeys(HORIZON_OUTCOMES, 0))
    touched = no_touch = unpriced_pre_first_touch = 0
    first_low_day_has_official_traded_close = 0
    day_of_first_low_official_close_above_reference = 0
    consecutive_close_breach_lag_days = []
    close_post_first_low = {}
    for origin, reference in sorted(plan["reference"].items()):
        days = [d for d in plan["days"] if (origin, d) in seen]
        positions = {d: seen[origin, d] for d in days}
        line = _amount(reference["reference_line_krw"])
        low_touch = (day(reference["first_observed_touch_on"])
                     if reference["first_observed_touch_on"] else None)
        close_touch = _first_close_breach(days, positions, line)
        close_post_first_low[origin] = close_touch
        if low_touch is None:
            no_touch += 1
            state = (
                "official_close_breach_without_public_low_touch"
                if close_touch else
                "no_public_low_touch_and_no_official_close_breach"
            )
        else:
            touched += 1
            if low_touch not in positions or positions[low_touch][0] != EXACT:
                state = "public_first_low_missing_official_traded_close"
            elif close_touch and close_touch < low_touch:
                state = "official_close_breach_before_public_first_low"
            else:
                first_low_day_has_official_traded_close += 1
                unpriced_pre_first_touch += sum(
                    positions[d][0] != EXACT for d in days if d < low_touch)
                if positions[low_touch][1] > line:
                    day_of_first_low_official_close_above_reference += 1
                if close_touch == low_touch:
                    state = "public_first_low_and_official_close_same_day"
                elif close_touch is not None:
                    state = "official_close_breach_later_than_public_first_low"
                    consecutive_close_breach_lag_days.append(
                        sum(low_touch < d <= close_touch for d in plan["days"]))
                else:
                    state = "public_first_low_but_no_official_close_breach"
        outcome[state] += 1

    for (origin, h), recorded in sorted(plan["horizon"].items()):
        end = plan["cutoff"][origin, h]
        days = [d for d in plan["days"]
                if d <= end and (origin, d) in seen]
        positions = {d: seen[origin, d] for d in days}
        low_touch = day(recorded["first_observed_touch_on"]) if recorded[
            "first_observed_touch_on"] else None
        close_touch = _first_close_breach(
            days, positions, _amount(plan["reference"][origin]["reference_line_krw"]))
        if low_touch is None:
            state = ("closing_breach_when_no_reported_low_touch"
                     if close_touch else
                     "no_low_touch_and_no_official_closing_breach_by_horizon")
        elif low_touch not in positions or positions[low_touch][0] != EXACT:
            state = "reported_low_touch_outside_traded_holder_path"
        elif close_touch and close_touch < low_touch:
            state = "closing_breach_precedes_reported_low_touch"
        elif close_touch:
            state = "low_touch_with_official_closing_breach_by_horizon"
        else:
            state = "low_touch_without_closing_breach_by_horizon"
        horizon_outcome[state] += 1

    passed = (not any(outcome[k] for k in BAD)
              and not any(horizon_outcome[k] for k in HORIZON_BAD)
              and first_low_day_has_official_traded_close == touched)
    return {
        "schema": SCHEMA,
        "original_issuers": 17,
        "horizons": 85,
        "official_close_observation_holder_days": len(observations),
        "reported_public_first_low_touch_issuers": touched,
        "reported_no_public_low_touch_issuers": no_touch,
        "first_low_dates_with_authenticated_official_close": first_low_day_has_official_traded_close,
        "first_low_dates_whose_official_close_is_above_reference":
            day_of_first_low_official_close_above_reference,
        "untraded_observations_before_reported_first_low": unpriced_pre_first_touch,
        "closing_breach_lag_sessions_after_first_low_total":
            sum(consecutive_close_breach_lag_days),
        "closing_breach_lag_sessions_after_first_low_max":
            max(consecutive_close_breach_lag_days, default=0),
        "origin_outcomes": dict(outcome),
        "horizon_outcomes": dict(horizon_outcome),
        "all_official_closing_breaches_consistent_with_frozen_public_first_lows": passed,
        "new_krx_market_api_requests": 0,
        "new_private_archive_reads": 0,
        "all_pre_touch_official_intraday_lows_verified": False,
        "first_intraday_reference_touch_certified": False,
        "continuous_adjusted_total_return_history_certified": False,
        "regular_only_volume_verified": False,
        "historical_pit_scores_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def valid_summary(x):
    if type(x) is not dict or x.get("schema") != SCHEMA:
        return False
    origin, horizon = x.get("origin_outcomes"), x.get("horizon_outcomes")
    if (type(origin) is not dict or set(origin) != set(OUTCOMES)
            or type(horizon) is not dict or set(horizon) != set(HORIZON_OUTCOMES)
            or any(type(v) is not int or v < 0 for v in
                   list(origin.values()) + list(horizon.values()))
            or sum(origin.values()) != 17 or sum(horizon.values()) != 85
            or x.get("original_issuers") != 17 or x.get("horizons") != 85
            or x.get("official_close_observation_holder_days") != 3461
            or (x.get("reported_public_first_low_touch_issuers")
                + x.get("reported_no_public_low_touch_issuers")) != 17
            or x.get("first_low_dates_with_authenticated_official_close") != x.get(
                "reported_public_first_low_touch_issuers")
            or x.get("all_official_closing_breaches_consistent_with_frozen_public_first_lows") is not True
            or any(origin[k] for k in BAD)
            or any(horizon[k] for k in HORIZON_BAD)
            or any(x.get(k) != 0 for k in (
                "new_krx_market_api_requests", "new_private_archive_reads"))
            or any(x.get(k) is not False for k in (
                "all_pre_touch_official_intraday_lows_verified",
                "first_intraday_reference_touch_certified",
                "continuous_adjusted_total_return_history_certified",
                "regular_only_volume_verified",
                "historical_pit_scores_verified", "performance_validated",
                "v30b_release_ready"))):
        return False
    return True
