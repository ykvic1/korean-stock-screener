"""OFFLINE industry-cycle fundamental and price-breadth features, not investment facts.

Accepts a declared complete cohort of issuer-specific constituents; corporate
and filing timestamps are UNTRUSTED, not independently verified KRX/DART PIT.
Never fill missing issuer profits/prices from surviving stocks or today's sector
mapping. Both metrics are all-or-none at declared cohort membership.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import math
import re
from zoneinfo import ZoneInfo
from v3_sector_rotation_signal import early_rotation_signal

SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"
MAX_MEMBERS = 3000
KST = ZoneInfo("Asia/Seoul")


class IndustryFeatureError(ValueError):
    """No private issuer financial data in error codes."""


def _date(v):
    if type(v) is not str or len(v) != 10:
        raise IndustryFeatureError("invalid_industry_date")
    try:
        d = date.fromisoformat(v)
    except ValueError:
        raise IndustryFeatureError("invalid_industry_date") from None
    if d.isoformat() != v:
        raise IndustryFeatureError("invalid_industry_date")
    return d


def _utc(v):
    if type(v) is not str or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", v):
        raise IndustryFeatureError("invalid_industry_utc_receipt")
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        raise IndustryFeatureError("invalid_industry_utc_receipt") from None


def _member_id(market, member):
    if not isinstance(member, dict):
        raise IndustryFeatureError("invalid_industry_constituent")
    if member.get("market") != market:
        raise IndustryFeatureError("mixed_industry_market")
    code, issuer = member.get("code"), member.get("issuer_id")
    if (type(code) is not str or not code.isascii() or not re.fullmatch(r"\d{6}", code)
            or type(issuer) is not str or not issuer.isascii()
            or not re.fullmatch(r"\d{8}", issuer)):
        raise IndustryFeatureError("invalid_constituent_identity")
    return (market, code, issuer)


def _profit_values(member, year, asof, decision):
    accounts = member.get("annual_operating_profit")
    if accounts is None:
        return None
    if not isinstance(accounts, list) or len(accounts) != 2:
        raise IndustryFeatureError("two_fiscal_profit_years_required")
    by_year = {}
    for row in accounts:
        if not isinstance(row, dict) or type(row.get("fiscal_year")) is not int:
            raise IndustryFeatureError("invalid_industry_profit_row")
        y = row["fiscal_year"]
        if y in by_year or y not in (year - 1, year):
            raise IndustryFeatureError("mixed_or_duplicate_profit_year")
        value = row.get("operating_profit_krw")
        if value is not None and (
                type(value) is not int or abs(value) > 10**19):
            raise IndustryFeatureError("invalid_operating_profit_amount")
        when = _date(row.get("published_on"))
        if when <= date(y, 12, 31):
            raise IndustryFeatureError("fiscal_profit_published_before_year_end")
        receipt = _utc(row.get("observed_at_utc"))
        # Published_on gives a DATE, receipt is an asserted actual UTC instant.
        if receipt.astimezone(KST).date() < when:
            raise IndustryFeatureError("statement_observed_before_publication")
        if when >= asof or receipt > decision:
            by_year[y] = None  # NO LOOKAHEAD: unknown at the decision.
        else:
            by_year[y] = value
    if set(by_year) != {year - 1, year}:
        raise IndustryFeatureError("missing_fiscal_pair")
    if None in by_year.values():
        return None
    return by_year[year - 1], by_year[year]


def _breadth_member(member, asof):
    rows = member.get("daily_closes")
    if rows is None:
        return None
    if not isinstance(rows, list) or len(rows) != 60:
        raise IndustryFeatureError("exactly_60_observed_closes_required")
    prior = None
    closes = []
    for row in rows:
        if not isinstance(row, dict) or row.get("session") != SESSION:
            raise IndustryFeatureError("invalid_industry_regular_session_label")
        d = _date(row.get("date"))
        if prior is not None and d <= prior or d > asof:
            raise IndustryFeatureError("future_or_duplicate_industry_close")
        if prior is not None and (d - prior).days > 14:
            raise IndustryFeatureError("unverified_large_constituent_gap")
        prior = d
        close = row.get("close")
        if type(close) not in (float, int) or not math.isfinite(close) or close <= 0:
            raise IndustryFeatureError("invalid_industry_close")
        closes.append(float(close))
    if prior != asof:
        return None
    return closes[-1] > sum(closes) / 60


def evaluate_industry_cohort(cohort, *, asof, decision_at_utc):
    """Return sector-level research metrics with declared all-member denominators.

    A company can be in only one (market, industry, asof) workbench sector.
    If ANY declared constituent lacks valid financial/price observations,
    that entire category stays null rather than survivor-subset rescaled.
    """
    day = _date(asof)
    decision = _utc(decision_at_utc)
    if (decision.astimezone(KST).date() != day
            or decision.astimezone(KST).time() < time(15, 30)):
        raise IndustryFeatureError("industry_signal_must_follow_close")
    if not isinstance(cohort, dict) or cohort.get("asof") != asof:
        raise IndustryFeatureError("industry_cohort_wrong_date")
    market, code = cohort.get("market"), cohort.get("industry_code")
    if (market not in ("KOSPI", "KOSDAQ") or type(code) is not str
            or not re.fullmatch("[A-Za-z0-9._-]{2,30}", code)):
        raise IndustryFeatureError("invalid_industry_identity")
    year = cohort.get("latest_comparable_fiscal_year")
    if type(year) is not int or not 1991 <= year < day.year:
        raise IndustryFeatureError("invalid_industry_fiscal_reference")
    members = cohort.get("members")
    if not isinstance(members, list) or not 2 <= len(members) <= MAX_MEMBERS:
        raise IndustryFeatureError("invalid_sector_member_roster")
    member_ids, profit_pairs, breadth_count = [], [], 0
    late_observed = 0
    all_profit, all_breadth = True, True
    for member in members:
        identity = _member_id(market, member)
        if identity in member_ids:
            raise IndustryFeatureError("duplicate_industry_member")
        member_ids.append(identity)
        start = _date(member.get("valid_from"))
        end = _date(member["valid_to"]) if member.get("valid_to") else None
        if end is not None and end <= start:
            raise IndustryFeatureError("invalid_constituent_lifecycle")
        # Never construct historical membership using current survivor list.
        if not(start <= day and (end is None or day < end)):
            raise IndustryFeatureError("member_not_effective_on_signal_date")
        membership_seen = _utc(member.get("membership_observed_at_utc"))
        if membership_seen > decision:
            late_observed += 1
            all_profit, all_breadth = False, False
            continue
        pairs = _profit_values(member, year, day, decision)
        if pairs is None:
            all_profit = False
        else:
            profit_pairs.append(pairs)
        active = _breadth_member(member, day)
        if active is None:
            all_breadth = False
        else:
            breadth_count += int(active)

    growth = None
    prev_sum = sum(pair[0] for pair in profit_pairs)
    now_sum = sum(pair[1] for pair in profit_pairs)
    risk_flags = []
    if all_profit and len(profit_pairs) == len(members):
        if prev_sum > 0:
            growth = round(100 * (now_sum / prev_sum - 1), 6)
        else:
            risk_flags.append("zero_or_loss_base_prevents_growth_ratio")
        if prev_sum > 0 and now_sum < prev_sum:
            risk_flags.append("aggregate_industry_operating_profit_declining")
    breadth = round(breadth_count / len(members), 6) if all_breadth else None
    # Independent 61-date industry-versus-market index history is optional:
    # a missing reference cannot turn into a fictitious five-point signal.
    rotation=None
    mk=cohort.get("market_index")
    idx=cohort.get("industry_index")
    if mk is not None or idx is not None:
        if not isinstance(mk,list) or not isinstance(idx,list):
            raise IndustryFeatureError("missing_one_industry_benchmark")
        benchmarks=[]
        for series in (mk,idx):
            if len(series)!=61:
                raise IndustryFeatureError("industry_requires_61_reference_dates")
            records={}
            previous=None
            for obs in series:
                if not isinstance(obs,dict):
                    raise IndustryFeatureError("invalid_industry_index_observation")
                stamp=_date(obs.get("date"))
                value=obs.get("close")
                if ((previous is not None and stamp<=previous) or stamp>day
                    or type(value) not in (int,float) or
                    not math.isfinite(value) or value<=0):
                    raise IndustryFeatureError("invalid_industry_index_observation")
                records[stamp.isoformat()]=value
                previous=stamp
            benchmarks.append(records)
        mkt,sector=benchmarks
        if list(mkt)!=list(sector):
            raise IndustryFeatureError("sector_market_calendar_mismatch")
        if list(mkt)[-1]!=asof:
            raise IndustryFeatureError("stale_sector_market_benchmark")
        rotation=early_rotation_signal(mkt,sector,list(mkt))
    # Sector-level improving-profit BREADTH measures the number of issuers
    # turning upward, not just how far the largest member has moved. Use
    # the full declared roster; do not give a favorable subset score when
    # any constituent's historical profit evidence is missing.
    improvement_breadth = None
    largest_improvement_share = None
    if all_profit and len(profit_pairs) == len(members):
        increases = [max(0, now - old) for old, now in profit_pairs]
        improvement_breadth = round(
            sum(now > old for old, now in profit_pairs) / len(members), 6)
        positive_sum = sum(increases)
        if positive_sum > 0:
            largest_improvement_share = round(max(increases) / positive_sum, 6)
            if largest_improvement_share > 0.7:
                risk_flags.append("profit_recovery_concentrated_in_one_issuer")
    if not all_breadth:
        rotation=None  # No full constituent roster, no industry price signal.
    quality=None
    if largest_improvement_share is not None and rotation is not None:
        concentration=(0 if largest_improvement_share>=0.70 else
                       1 if largest_improvement_share>=0.45 else 2)
        overheat_free=int(rotation["recent20"]<12)
        quality=round((concentration+overheat_free)/3,6)
    if breadth is not None and breadth < 0.3:
        risk_flags.append("low_declared_cohort_above_ma_breadth")
    return {
        "schema": "v3-industry-raw-cycle-0.1",
        "asof": asof, "market": market, "industry_code": code,
        "declared_member_identities": [
            {"market": a, "code": b, "issuer_id": c} for a,b,c in member_ids
        ],
        "declared_member_count": len(members),
        "profit_covered_issuers": len(profit_pairs),
        "breadth_covered_issuers": (
            len(members) if all_breadth else None
        ),
        "late_membership_sources": late_observed,
        "features": {
            "industry_profit_growth_12m_pct": growth,
            "industry_breadth_above_ma_ratio": breadth,
            "industry_profit_improving_issuer_ratio": improvement_breadth,
            "industry_recovery_largest_issuer_delta_share":
                largest_improvement_share,
            "industry_early_rotation_signal":
                rotation["strength"] if rotation is not None else None,
            "industry_overheat_concentration_quality": quality,
        },
        "risk_flags_research": sorted(risk_flags),
        "only_declared_cohort_checked": True,
        "full_delisted_sector_universe_authenticated": False,
        "sector_membership_original_pit_verified": False,
        "annual_operating_profit_original_pit_verified": False,
        "price_corporate_action_and_session_verified": False,
        "official_market_sector_definition_verified": False,
        "v3_scoring_authorized": False,
        "confirmed_buy": False,
    }
