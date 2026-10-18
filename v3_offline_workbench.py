"""Runnable v3 OFFLINE RESEARCH workbench: raw -> features -> 6 pillars -> risk.

It deliberately does NOT publish recommendations, compute confirmed ranking,
read live v2.2 screens, fetch providers, place orders or authenticate inputs.
The --demo generates SYNTHETIC market data. External raw metrics remain
untrusted until market, PIT and corporate-action certification exists.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import sys
from pathlib import Path

from v3_research_factor_engine import (
    FIELD_SPECS, FactorEngineError, ias_raw_features_from_ledger,
    research_factor_batch
)
from v3_price_feature_adapter import extract_price_features, PriceFeatureError
from v3_portfolio_risk import evaluate_portfolio, PortfolioRiskError
from v3_financial_feature_adapter import (
    calculate_financial_raw_features, FinancialFeatureError
)
from v3_industry_feature_adapter import (
    evaluate_industry_cohort, IndustryFeatureError
)
from v3_factor_overlap_audit import audit_factor_overlap, FeatureOverlapError

MAX_BATCH = 20000
MAX_INPUT_BYTES = 15_000_000
# Financial/valuation/industry evidence must be sourced independently later.
MANUAL_FIELDS = frozenset(
    name for name in FIELD_SPECS
    if name.startswith(("financial_", "valuation_"))
    or name in ("industry_profit_growth_12m_pct",
                "industry_breadth_above_ma_ratio")
)


class WorkbenchError(ValueError):
    """Only fixed reason names: never expose private raw numbers or paths."""


def _reject_unknown_metrics(manual):
    if not isinstance(manual, dict) or set(manual) - MANUAL_FIELDS:
        raise WorkbenchError("unknown_or_unauthed_manual_metric")


def run_workbench(bundle, *, min_peers=30):
    if not isinstance(bundle, dict):
        raise WorkbenchError("invalid_workbench_input")
    asof, stocks = bundle.get("asof"), bundle.get("stocks")
    if (type(asof) is not str or not isinstance(stocks, list)
            or len(stocks) > MAX_BATCH):
        raise WorkbenchError("invalid_asof_or_cohort")
    factor_input = []
    input_issue_counts = Counter()
    # Each stock with a declared sector must match exactly one full
    # research cohort. No fallback to surviving members of today's universe.
    derived_industry = {}
    sectors = bundle.get("industry_cohorts")
    if sectors is not None:
        if not isinstance(sectors, list) or len(sectors) > MAX_BATCH:
            raise WorkbenchError("invalid_industry_cohorts")
        target_members = {}
        for item in stocks:
            if not isinstance(item, dict):
                raise WorkbenchError("invalid_industry_stock")
            sector_code = item.get("industry_code")
            if sector_code is not None:
                key = (item.get("market"), sector_code)
                target_members.setdefault(key, set()).add(
                    (item.get("market"), item.get("code"), item.get("issuer_id")))
        visited = set()
        for sector in sectors:
            if not isinstance(sector, dict):
                raise WorkbenchError("invalid_industry_cohort")
            key = (sector.get("market"), sector.get("industry_code"))
            if key in visited:
                raise WorkbenchError("duplicate_industry_cohort")
            visited.add(key)
            try:
                output = evaluate_industry_cohort(
                    sector, asof=asof,
                    decision_at_utc=asof + "T07:10:00Z")
            except (IndustryFeatureError, KeyError, TypeError):
                raise WorkbenchError("unusable_industry_member_evidence") from None
            included = {
                (x["market"], x["code"], x["issuer_id"])
                for x in output["declared_member_identities"]
            }
            if included != target_members.get(key, set()):
                raise WorkbenchError("industry_member_roster_not_same_research_cohort")
            for identity in included:
                if identity in derived_industry:
                    raise WorkbenchError("industry_member_assigned_twice")
                derived_industry[identity] = output["features"]
            if output["risk_flags_research"]:
                input_issue_counts["industry_cycle_warning_cohorts"] += 1
        if set(target_members) != visited:
            raise WorkbenchError("industry_classification_missing_cohort")
    for stock in stocks:
        if not isinstance(stock, dict) or stock.get("asof") != asof:
            raise WorkbenchError("mixed_snapshot_dates")
        code, issuer, market = (
            stock.get("code"), stock.get("issuer_id"), stock.get("market"))
        manual = stock.get("manual_raw_features", {})
        _reject_unknown_metrics(manual)
        merged = dict(manual)
        industry_derived = derived_industry.get((market, code, issuer))
        if industry_derived is not None:
            if set(industry_derived) & set(manual):
                raise WorkbenchError("manual_industry_metric_conflicts_with_sector")
            merged.update(industry_derived)
        statements = stock.get("annual_statements")
        if statements is not None:
            try:
                finance = calculate_financial_raw_features(
                    statements, asof=asof, issuer_id=issuer,
                    market_cap=stock.get("market_cap"))
            except (FinancialFeatureError, KeyError, TypeError):
                raise WorkbenchError("unusable_pit_financial_observations") from None
            if set(finance["features"]) & set(manual):
                raise WorkbenchError("manual_financial_metric_conflicts_with_statements")
            merged.update(finance["features"])
            if finance["financial_red_flag_candidates"]:
                input_issue_counts["financial_warning_flags"] += 1
        price = stock.get("price_bars")
        obv, price_r60 = None, None
        if price is not None:
            try:
                prices = extract_price_features(
                    price, asof=asof,
                    market_index=stock.get("market_index"),
                    industry_index=stock.get("industry_index"))
            except (PriceFeatureError, KeyError, TypeError):
                raise WorkbenchError("unusable_price_observations") from None
            merged.update(prices["features"])
            obv = prices["features"]["ias_obv_change_60_pct"]
            if len(price) >= 61:
                price_r60 = 100 * (
                    price[-1]["close"] / price[-61]["close"] - 1)
            if prices["price_extension_caution"]:
                input_issue_counts["price_extension_flags"] += 1
        flow = stock.get("flow_ledger")
        if flow is not None:
            if any(flow.get(k) != v for k, v in (
                    ("code", code), ("issuer_id", issuer), ("market", market))):
                raise WorkbenchError("cross_issuer_investor_flow")
            try:
                features = ias_raw_features_from_ledger(
                    flow, asof=asof,
                    float_shares=stock.get("float_shares"),
                    ownership_delta_250_pp=stock.get("ownership_delta_250_pp"),
                    obv_change_60_pct=obv,
                    price_return_60_pct=price_r60)
            except (FactorEngineError, KeyError, TypeError):
                raise WorkbenchError("unusable_ias_flow_input") from None
            # Same OBV field is derived by the price adapter; never silently
            # overwrite a contradictory externally supplied value.
            for key, val in features.items():
                if key in merged and merged[key] is not None and (
                        val is not None and val != merged[key]):
                    raise WorkbenchError("conflicting_derived_indicator")
                merged[key] = val
        factor_input.append({
            "code": code, "issuer_id": issuer, "market": market,
            "asof": asof, "features": merged
        })
    try:
        research = research_factor_batch(
            factor_input, asof=asof, min_peers=min_peers)
    except FactorEngineError as exc:
        # FactorEngineError only has static safe reason codes. Preserve the
        # category for offline operator triage, never include raw metrics.
        raise WorkbenchError("factor_batch_not_validated:" + str(exc)) from None

    # High correlation between nested windows is recorded, not fixed by
    # multiplying original evidence or silently adjusting agreed weights.
    try:
        overlap = audit_factor_overlap(factor_input, asof=asof)
    except FeatureOverlapError:
        raise WorkbenchError("overlap_diagnostics_unusable") from None

    # User-declared hypothetical loss controls are separate from score.
    plans = bundle.get("hypothetical_portfolio_plans")
    budget = bundle.get("hypothetical_nav_risk_budget_pct")
    portfolio = None
    if plans is not None or budget is not None:
        if (not isinstance(plans, list) or not plans or budget is None):
            raise WorkbenchError("incomplete_portfolio_risk_inputs")
        known_codes = {row["code"] for row in factor_input}
        if any(not isinstance(plan, dict) or plan.get("code") not in known_codes
               for plan in plans):
            raise WorkbenchError("portfolio_unknown_research_symbol")
        try:
            portfolio = evaluate_portfolio(
                plans, total_nav_risk_budget_pct=budget)
        except PortfolioRiskError:
            raise WorkbenchError("invalid_hypothetical_portfolio") from None
    rows = research["rows"]
    return {
        "schema": "v3-offline-executable-workbench-0.1",
        "asof": asof, "research_stock_count": len(rows),
        "market_cohort_sizes": research["market_cohorts"],
        "fully_covered_six_pillar_research_only": sum(
            x["candidate_full_total_research_only"] is not None for x in rows),
        "no_input_evidence_or_partial": sum(
            x["candidate_full_total_research_only"] is None for x in rows),
        "input_issue_counts": dict(sorted(input_issue_counts.items())),
        "factor_overlap_diagnostics_research_only": overlap,
        "stocks_in_code_order_not_ranked": sorted(
            rows, key=lambda x: (x["market"], x["code"])),
        "hypothetical_portfolio_risk_only": portfolio,
        "raw_price_or_investor_flows_publicly_returned": False,
        "live_krx_or_dart_api_called": False,
        "historical_free_float_and_pit_authenticated": False,
        "metric_normalization_empirically_validated": False,
        "confirmed_stock_ranks": None,
        "expected_returns": None,
        "recommended_positions": None,
        "confirmed_buy": False,
        "v3_release_ready": False,
    }


def demo_bundle(*, sample_each_market=32):
    """Clearly synthetic, deterministic complete dry-run to exercise all 6."""
    if type(sample_each_market) is not int or not 30 <= sample_each_market <= 100:
        raise WorkbenchError("invalid_demo_cohort_size")
    asof = date(2026, 10, 16)
    days = []
    t = asof
    while len(days) < 650:
        if t.weekday() < 5:
            days.append(t.isoformat())
        t -= timedelta(days=1)
    days.reverse()
    markets = ["KOSPI", "KOSDAQ"]
    result = []
    industry_cohorts = []
    for market_index, market_name in enumerate(markets):
        market_benchmark = [
            {"date": d, "close": 1000 + 0.025 * j}
            for j, d in enumerate(days)]
        industry_benchmark = [
            {"date": d, "close": 800 + 0.04 * j}
            for j, d in enumerate(days)]
        for i in range(sample_each_market):
            code = f"{market_index * 100000 + i + 1:06d}"
            issuer = f"{market_index * 10000000 + i + 1:08d}"
            drift = 0.035 + 0.002 * i
            bars = []
            for j, day in enumerate(days):
                close = 90 + drift*j + i
                bars.append({
                    "date": day, "session": "KRX_REGULAR_0900_1530_ASIA_SEOUL",
                    "open": close - 0.1, "high": close + 0.5,
                    "low": close - 0.5, "close": close,
                    "volume": 1000 + 10*i + j})
            flow_days = []
            for day in days[-250:]:
                flow_days.append({
                    "date": day, "session": "KRX_REGULAR_0900_1530_ASIA_SEOUL",
                    "market": market_name,
                    "foreign_net_shares": 10 + i,
                    "institution_total_net_shares": 20 + i,
                    "pension_net_shares": 2 + (i % 4)})
            ledger = {
                "code": code, "issuer_id": issuer, "market": market_name,
                "institution_definition": "total_includes_pension",
                "pension_definition": "pension_etc_subcategory",
                "observations": flow_days}
            # These four annual statements are fully SYNTHETIC; actual
            # OpenDART fiscal scope, revisions and PIT require later proof.
            annuals = []
            for year in range(2022, 2026):
                k = year - 2022
                revenue = 100_000_000_000 + i*1_000_000_000 + k*5_000_000_000
                operating_income = round(revenue*(0.09 + i/2000))
                assets = 180_000_000_000 + i*2_000_000_000 + k*4_000_000_000
                liabilities = 80_000_000_000 + i*1_000_000_000 + k*1_000_000_000
                annuals.append({
                    "issuer_id": issuer, "fiscal_year": year, "scope": "CFS",
                    "currency": "KRW", "published_on": f"{year+1}-03-01",
                    "rcept_no": f"{year+1}0301{i+1:06d}",
                    "original_sha256": "a"*64,
                    "accounts": {
                        "revenue_krw": revenue,
                        "operating_income_krw": operating_income,
                        "operating_cashflow_krw": 16_000_000_000 + i*100_000_000 + k*200_000_000,
                        "capex_cash_outflow_krw": 4_000_000_000 + k*100_000_000,
                        "assets_krw": assets, "liabilities_krw": liabilities,
                        "equity_krw": assets - liabilities,
                        "owners_equity_krw": assets - liabilities - 5_000_000_000,
                        "owners_profit_krw": 8_000_000_000 + i*70_000_000 + k*100_000_000,
                        "cash_krw": 20_000_000_000,
                        "interest_bearing_debt_krw": 50_000_000_000 + i*500_000_000
                    }
                })
            manual = {}
            result.append({
                "code": code, "issuer_id": issuer, "market": market_name,
                "industry_code": f"IND.{market_index+1}",
                "asof": asof.isoformat(), "price_bars": bars,
                "market_index": market_benchmark,
                "industry_index": industry_benchmark,
                "flow_ledger": ledger, "float_shares": 1_000_000 + 10000*i,
                "ownership_delta_250_pp": 0.2 + i / 100,
                "annual_statements": annuals,
                "market_cap": {"asof": asof.isoformat(),
                               "amount_krw": 115_000_000_000 + i*1_000_000_000},
                "manual_raw_features": manual,
            })
        group = [item for item in result if item["market"] == market_name]
        industry_members = []
        for item in group:
            annual = item["annual_statements"][-2:]
            industry_members.append({
                "market": market_name, "code": item["code"],
                "issuer_id": item["issuer_id"],
                "valid_from": "2020-01-01", "valid_to": None,
                "membership_observed_at_utc": asof.isoformat() + "T05:00:00Z",
                "daily_closes": [
                    {"date": row["date"], "close": row["close"],
                     "session": row["session"]}
                    for row in item["price_bars"][-60:]
                ],
                "annual_operating_profit": [
                    {"fiscal_year": statement["fiscal_year"],
                     "published_on": statement["published_on"],
                     "observed_at_utc": statement["published_on"] + "T01:00:00Z",
                     "operating_profit_krw": statement["accounts"]["operating_income_krw"]}
                    for statement in annual
                ],
            })
        industry_cohorts.append({
            "asof": asof.isoformat(),
            "market": market_name, "industry_code": f"IND.{market_index+1}",
            "latest_comparable_fiscal_year": 2025,
            "members": industry_members,
        })
    return {"asof": asof.isoformat(), "stocks": result,
            "industry_cohorts": industry_cohorts}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Research-only v3 six-factor dry-run, no official API or buy signal")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true",
                        help="Generate synthetic 64 stock records for validation")
    source.add_argument("--input", type=Path,
                        help="Private local JSON, never a live/public feed")
    args = parser.parse_args(argv)
    try:
        if args.demo:
            bundle = demo_bundle()
        else:
            if not args.input.is_file() or args.input.stat().st_size > MAX_INPUT_BYTES:
                raise WorkbenchError("invalid_private_input_file")
            bundle = json.loads(args.input.read_text(encoding="utf-8"))
        report = run_workbench(bundle)
    except (WorkbenchError, json.JSONDecodeError, OSError, UnicodeError):
        print('{"status":"offline_research_blocked","confirmed_buy":false}')
        return 1
    print(json.dumps(
        {"dataset": "SYNTHETIC_DEMO" if args.demo else "UNVERIFIED_LOCAL_INPUT",
         **report}, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
