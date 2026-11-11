"""Publish the same dated personal stability calculation used by the screen.

Five factor budgets remain 20/25/20/20/15. Retained observations keep their
dates. Stop distance is information only and must never affect scores/ranks.
"""
from __future__ import annotations

import csv
from datetime import date
from io import StringIO
import math

from v3_dart_bulk_history import _stable_digest
from v3_issuer_factor_ledger import BUDGET, SCHEMA as LEDGER_SCHEMA, _factor
from v3_eps_valuation import valid as valid_valuation, MODEL as EPS_MODEL

SCHEMA = "v3-personal-stability-adjusted-score-v4"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def rounded(value, places=1, *, epsilon=True):
    # Match the existing JavaScript Math.round((n + EPSILON) * scale),
    # including negative half ties; Python's built-in round uses even ties.
    return math.floor((value + (2.220446049250313e-16 if epsilon else 0)) * 10**places + .5) / 10**places


def profile(row, earned):
    row = row or {}
    cap = row.get("market_cap_eok")
    cap = cap if finite(cap) and cap > 0 else None
    capital = 0 if cap is None else rounded(5 * max(0, 1 - cap / 5000))
    price, stop, reported = row.get("price"), row.get("stop_line"), row.get("stop_pct")
    distance = None
    if (finite(price) and price > 0 and finite(stop) and 0 < stop < price
        and finite(reported)):
        raw = 100 * (price - stop) / price
        if abs(reported + raw) <= 2:
            distance = rounded(raw, 2, epsilon=False)
    # Retain a zero-valued compatibility field for dated CSV consumers. No
    # stop reference, stop distance or prior stop-risk flag can move a score.
    adjustment = 0
    deduction = capital
    return {
        "capital_deduction": capital, "stop_adjustment": adjustment,
        "stop_distance_pct": distance, "deduction": deduction,
        "adjusted_score": max(0, min(100, rounded(earned - deduction))) if finite(earned) else None,
        "missing_adjustment_sources": int(cap is None),
        "single_day_trading_value_points": 0,
    }


def build(market, catalog, ledger, *, valuation_overrides=None):
    day = catalog.get("date")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("invalid_personal_score_source_day") from None
    if (market.get("date") != day or ledger.get("market_asof") != day
        or ledger.get("schema") != LEDGER_SCHEMA
        or ledger.get("investment_score_certified") is not False
        or ledger.get("mixed_date_score_research_only") is not True
        or ledger.get("missing_is_not_zero") is not True
        or not isinstance(ledger.get("rows"), dict)
        or not isinstance(catalog.get("rows"), list)
        or catalog.get("listed_total") != len(catalog["rows"])
        or ledger.get("listed_total") != catalog.get("listed_total")):
        raise ValueError("unaligned_personal_score_sources")
    stocks = {}
    for stock in market.get("stocks", []):
        code = stock.get("code")
        if code in stocks or stock.get("price_date") != day:
            raise ValueError("invalid_personal_score_price_identity")
        stocks[code] = stock
    output, seen, applied_overrides = [], set(), {}
    for member in catalog["rows"]:
        code = member["code"]
        saved = ledger["rows"].get(code)
        if (code in seen or not isinstance(saved, dict) or saved.get("active") is not True
            or any(saved.get(key) != member.get(key) for key in ("code", "name", "market"))):
            raise ValueError("invalid_personal_score_issuer_identity")
        seen.add(code)
        factors = {}
        for key, value in saved.get("factors", {}).items():
            checked = _factor(value, day)
            if key not in BUDGET or checked is None or checked["available"] > BUDGET[key]:
                raise ValueError("invalid_personal_score_retained_factor")
            factors[key] = checked
        value_model = 'legacy-observed-valuation20'
        override = (valuation_overrides or {}).get(code)
        if valid_valuation(override, code=code, name=member['name'], market=member['market'], day=day):
            applied_overrides[code] = override
            factors['valuation'] = {'points': override['points'], 'available': 20,
                                    # Display the earliest dated component, not
                                    # today's recomputation as if every FCF/PBR
                                    # input had been freshly observed.
                                    'asof': override['source_oldest_asof'], 'source': EPS_MODEL}
            value_model = EPS_MODEL
        available = sum(x["available"] for x in factors.values())
        # Browser PILLARS order and left-to-right IEEE addition are part of
        # the displayed contract. Dict order/Python 3.12's compensated sum
        # otherwise changes genuine half-tie totals by 0.0001.
        subtotal = 0.0
        for key in BUDGET:
            subtotal += factors.get(key, {}).get("points", 0)
        earned = rounded(subtotal, 4, epsilon=False) if available else None
        stock = stocks.get(code)
        if stock and any(stock.get(key) != member.get(key) for key in ("name", "market")):
            raise ValueError("invalid_personal_score_price_identity")
        adjustment = profile(stock, earned)
        output.append({"code": code, "name": member["name"], "market": member["market"],
                       "asof": day, "price_asof": day if stock is not None else None,
                       "base_score": earned, "available": available,
                       "factors": factors, "price_observed": stock is not None,
                       "oldest_factor_asof": min((v["asof"] for v in factors.values()), default=None),
                       "retained_factor_count": sum(v["asof"] != day for v in factors.values()),
                       "personal_rank": None, **adjustment})
        output[-1]['valuation_model'] = value_model
    if not set(stocks) <= seen:
        raise ValueError("unlisted_personal_score_price_identity")
    eligible = [row for row in output if row["price_observed"] and row["base_score"] is not None]
    eligible.sort(key=lambda r: (-r["available"], -r["adjusted_score"],
                                r["missing_adjustment_sources"], -r["base_score"], r["code"]))
    for rank, row in enumerate(eligible, 1):
        row["personal_rank"] = rank
    summary = {
        "schema": SCHEMA, "asof": day, "listed_total": len(output),
        "ranked_issuers": len(eligible), "factor_budgets": dict(BUDGET),
        "capital_deduction_max": 5, "stop_adjustment_range": [0, 0],
        "structural_stop_reference_scored": False,
        "single_day_trading_value_points": 0, "industry_points": 0,
        "mixed_date_factor_values_keep_original_dates": True,
        "missing_is_not_zero": True, "stop_hit_probability_calibrated": False,
        "official_investment_scores_certified": False, "provider_api_calls": 0,
        "source_sha256": {"market": _stable_digest(market), "catalog": _stable_digest(catalog),
                          "issuer_factors": _stable_digest(ledger)},
        "valuation_overrides": applied_overrides,
        "reported_eps_valuation_issuers": sum(r['valuation_model'] == EPS_MODEL for r in output),
    }
    return output, summary


def to_csv(rows):
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    fields = ["안정형순위", "종목코드", "종목명", "시장", "가격기준일", "점수산식버전",
              "안정형보정점수", "보정전관측합계", "산출가능배점", "시총감점", "손절거리보정_미적용",
              "손절거리퍼센트", "미확인보정근거수", "최초팩터기준일", "과거팩터유지수", "가치점수모형"]
    for key in BUDGET:
        fields += [key + "_점수", key + "_확보배점", key + "_기준일"]
    writer.writerow(fields)
    for row in sorted(rows, key=lambda r: (r["personal_rank"] is None, r["personal_rank"] or 0, r["code"])):
        values = [row["personal_rank"], row["code"], row["name"], row["market"], row["price_asof"], SCHEMA,
                  row["adjusted_score"], row["base_score"], row["available"], row["capital_deduction"],
                  row["stop_adjustment"], row["stop_distance_pct"], row["missing_adjustment_sources"],
                  row["oldest_factor_asof"], row["retained_factor_count"], row['valuation_model']]
        for key in BUDGET:
            factor = row["factors"].get(key, {})
            values += [factor.get("points"), factor.get("available"), factor.get("asof")]
        writer.writerow(values)
    return "\ufeff" + buffer.getvalue()
