"""Corroborate reviewed split/reduction share-count anchors against saved KRX closes.

Read-only and private: caller supplies official archived daily rows in memory.
Public output contains aggregate outcomes only, never security codes, share
counts, or licensed closes. This is *not* an adjusted-price path certification.
"""
from bisect import bisect_left
from collections import Counter
from fractions import Fraction
import re

from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import day, require

SCHEMA = "v3-historical-official-reviewed-share-anchors-v1"
ACTIONS = ("split", "reverse_split", "capital_reduction")
STAGES = ("prior_session", "listing_session")
OUTCOMES = ("exact", "missing_official_archive_session", "public_row_missing",
            "official_row_missing", "both_rows_missing", "identity_mismatch",
            "official_listed_shares_mismatch", "public_listed_shares_mismatch")


def plan_events(rows, sessions):
    """Return session-indexed PRIVATE expected anchors, without market requests."""
    require(type(rows) is list and isinstance(sessions, list) and sessions == sorted(set(sessions))
            and len(sessions) > 1, "invalid_reviewed_share_event_sessions")
    by_session = {}
    seen = set()
    for number, row in enumerate(rows):
        require(type(row) is dict and row.get("action") in ACTIONS
                and type(row.get("code")) is str
                and re.fullmatch(r"[0-9A-Z]{6}", row["code"])
                and type(row.get("market")) is str
                and row["market"] in ("KOSPI", "KOSDAQ")
                and type(row.get("name")) is str and row["name"].strip(),
                "invalid_reviewed_share_event_identity")
        listing_on = day(row["listing_on"])
        key = (listing_on, row["code"])
        require(key not in seen, "duplicate_reviewed_share_event")
        seen.add(key)
        position = bisect_left(sessions, listing_on)
        require(0 < position < len(sessions) and sessions[position] == listing_on,
                "reviewed_share_event_outside_archive_calendar")
        try:
            prior_shares = int(row["shares_before"])
            after_shares = int(row["shares_after"])
            new = int(row["new_units"])
            old = int(row["old_units"])
        except (ValueError, TypeError, KeyError):
            require(False, "invalid_reviewed_share_event_ratio")
        require(prior_shares > 0 and after_shares > 0 and new > 0 and old > 0
                and after_shares == (prior_shares * new) // old
                and (new > old) == (row["action"] == "split")
                and (new < old) == (row["action"] != "split")
                and type(row.get("notice_url")) is str
                and row["notice_url"].startswith("https://kind.krx.co.kr/"),
                "invalid_reviewed_share_event_ratio")
        for session, stage, expected in (
            (sessions[position - 1], "prior_session", prior_shares),
            (listing_on, "listing_session", after_shares),
        ):
            by_session.setdefault(session, []).append(
                (number, stage, row["code"], row["market"], row["name"], expected))
    return by_session, len(rows)


def compare_anchor(public, official, event):
    """Compare only SAME issuer + exchange, independently against KRX shares."""
    _, _, code, market, name, expected = event
    source, saved = public.get(code), official.get(code)
    if source is None:
        return "both_rows_missing" if saved is None else "public_row_missing"
    if saved is None:
        return "official_row_missing"
    if (source[0] != market or saved[0] != market
            or not normalized_name(name)
            or normalized_name(source[1]) != normalized_name(name)
            or normalized_name(saved[1]) != normalized_name(name)):
        return "identity_mismatch"
    if type(saved[3]) is not int or saved[3] != expected:
        return "official_listed_shares_mismatch"
    if type(source[2]) not in (int, float) or source[2] != expected:
        return "public_listed_shares_mismatch"
    return "exact"


def observation_rows(public, official, session_events):
    """PRIVATE ephemeral tuples removed before publication."""
    return [(number, stage, compare_anchor(public, official, event))
            for event in session_events for number, stage, *_ in (event,)]


def summarize(event_count, observations):
    """Emit no issuer-level shares, codes, or prices."""
    require(type(event_count) is int and event_count >= 0,
            "invalid_reviewed_share_event_count")
    results = {number: {stage: "missing_official_archive_session"
                        for stage in STAGES} for number in range(event_count)}
    for number, stage, outcome in observations:
        require(type(number) is int and number in results and stage in STAGES
                and outcome in OUTCOMES
                and results[number][stage] == "missing_official_archive_session",
                "invalid_reviewed_share_event_anchor_observation")
        results[number][stage] = outcome
    by_stage = {stage: Counter({outcome: 0 for outcome in OUTCOMES})
                for stage in STAGES}
    by_pair = Counter()
    for states in results.values():
        for stage in STAGES:
            by_stage[stage][states[stage]] += 1
        by_pair["|".join(states[s] for s in STAGES)] += 1
    both = by_pair["exact|exact"]
    return {
        "schema": SCHEMA,
        "reviewed_events": event_count,
        "prior_anchor_exact_shares": by_stage["prior_session"]["exact"],
        "listing_anchor_exact_shares": by_stage["listing_session"]["exact"],
        "both_anchor_exact_shares": both,
        "all_adjacent_share_anchors_match": event_count > 0 and both == event_count,
        "by_stage": {stage: dict(by_stage[stage]) for stage in STAGES},
        "by_pair": dict(sorted(by_pair.items())),
        "new_krx_provider_calls": 0,
        "not_a_shareholder_adjusted_price_path": True,
        "adjusted_price_history_verified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
    }


def valid_summary(report):
    if type(report) is not dict or report.get("schema") != SCHEMA:
        return False
    n = report.get("reviewed_events")
    if type(n) is not int or n < 0:
        return False
    stages = report.get("by_stage")
    pairs = report.get("by_pair")
    if type(stages) is not dict or set(stages) != set(STAGES) or type(pairs) is not dict:
        return False
    for stage in STAGES:
        counts = stages[stage]
        if (type(counts) is not dict or set(counts) != set(OUTCOMES)
                or any(type(value) is not int or value < 0 for value in counts.values())
                or sum(counts.values()) != n
                or report.get(("prior_anchor_exact_shares" if stage == STAGES[0]
                               else "listing_anchor_exact_shares")) != counts["exact"]):
            return False
    if any(type(key) is not str or type(val) is not int or val < 0
           or len(key.split("|")) != 2 or any(x not in OUTCOMES for x in key.split("|"))
           for key, val in pairs.items()) or sum(pairs.values()) != n:
        return False
    for i, stage in enumerate(STAGES):
        if any(sum(value for key, value in pairs.items() if key.split("|")[i] == outcome)
               != stages[stage][outcome] for outcome in OUTCOMES):
            return False
    both = pairs.get("exact|exact", 0)
    return (report.get("both_anchor_exact_shares") == both
            and report.get("all_adjacent_share_anchors_match") is (n > 0 and both == n)
            and report.get("new_krx_provider_calls") == 0
            and report.get("not_a_shareholder_adjusted_price_path") is True
            and report.get("adjusted_price_history_verified") is False
            and report.get("point_in_time_scores_verified") is False
            and report.get("performance_validated") is False)
