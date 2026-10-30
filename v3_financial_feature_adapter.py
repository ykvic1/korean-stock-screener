"""Offline annual KRW statement -> actual financial/valuation RAW features.

This computes ratios from observed, receipt-labeled annual values, NOT
authenticated DART point-in-time facts. Receipt/date/hash are caller assertions
and not independently verified; no v3 confirmed score or live API access.
"""
from __future__ import annotations

from datetime import date
import math
import re

FINANCIAL_FIELDS = (
    "revenue_krw", "operating_income_krw", "operating_cashflow_krw",
    "capex_cash_outflow_krw", "assets_krw", "liabilities_krw",
    "equity_krw", "owners_equity_krw", "owners_profit_krw",
    "cash_krw", "interest_bearing_debt_krw"
)
NONNEGATIVE_FIELDS = {
    "capex_cash_outflow_krw", "assets_krw", "liabilities_krw",
    "cash_krw", "interest_bearing_debt_krw",
}
FINANCIAL_OUT = (
    "financial_revenue_cagr_3y_pct",
    "financial_operating_margin_3y_pct",
    "financial_fcf_assets_3y_pct",
    "financial_net_debt_equity_pct",
)
VALUATION_OUT = (
    "valuation_pbr", "valuation_earnings_yield_pct",
    "valuation_fcf_yield_pct",
)


class FinancialFeatureError(ValueError):
    """Sanitized static error reasons, no source monetary values in logs."""


def _day(value):
    if type(value) is not str or len(value) != 10:
        raise FinancialFeatureError("invalid_financial_date")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise FinancialFeatureError("invalid_financial_date") from None
    if d.isoformat() != value:
        raise FinancialFeatureError("invalid_financial_date")
    return d


def _money(value, *, field):
    if value is None:
        return None
    if type(value) is not int or abs(value) > 10**19:
        raise FinancialFeatureError("invalid_krw_numeric_amount")
    if field in NONNEGATIVE_FIELDS and value < 0:
        raise FinancialFeatureError("negative_nonnegative_account")
    return value


def _ratio(num, den, *, scale=1):
    if num is None or den is None or den <= 0:
        return None
    return round(scale * (num / den), 6)


def calculate_financial_raw_features(statements, *, asof, issuer_id,
                                     market_cap=None):
    """Use 4-5 consecutive annually published fiscal reports, CFS or OFS.

    3-year revenue CAGR uses 4 year-end amounts; last 3 fiscal years for
    average operating margin and average FCF/assets. Market cap uses a
    CALLER-SUPPLIED share count and price at same asof (not verified here).
    """
    cutoff = _day(asof)
    if (type(issuer_id) is not str or not re.fullmatch("[0-9]{8}", issuer_id)
            or not isinstance(statements, list)
            or not 4 <= len(statements) <= 5):
        raise FinancialFeatureError("missing_four_year_issuer_cohort")
    seen_years, seen_receipts, scopes, records = set(), set(), set(), []
    for row in statements:
        if not isinstance(row, dict) or row.get("issuer_id") != issuer_id:
            raise FinancialFeatureError("cross_issuer_statement")
        year = row.get("fiscal_year")
        if type(year) is not int or year < 1990 or year > cutoff.year:
            raise FinancialFeatureError("invalid_fiscal_year")
        if year in seen_years:
            raise FinancialFeatureError("duplicate_fiscal_year_version")
        seen_years.add(year)
        if row.get("scope") not in ("CFS", "OFS"):
            raise FinancialFeatureError("unknown_statement_scope")
        scopes.add(row["scope"])
        if row.get("currency") != "KRW":
            raise FinancialFeatureError("non_krw_financial_statement")
        if (type(row.get("rcept_no")) is not str
                or not re.fullmatch("[0-9]{14}", row["rcept_no"])
                or type(row.get("original_sha256")) is not str
                or not re.fullmatch("[a-f0-9]{64}", row["original_sha256"])):
            raise FinancialFeatureError("statement_receipt_or_digest_missing")
        filing = _day(row.get("published_on"))
        # One annual report per fiscal year: a single receipt carrying
        # comparative tables must not impersonate four separate filings.
        receipt_no = row["rcept_no"]
        if receipt_no in seen_receipts:
            raise FinancialFeatureError("reused_annual_receipt_across_years")
        seen_receipts.add(receipt_no)
        # The official list API reports rcept_dt independently of rcept_no.
        # Compare it to the caller's publication date when that source field
        # is available; NEVER infer the date from receipt-number digits.
        received = row.get("rcept_dt")
        if received is not None and (
                type(received) is not str or len(received) != 8
                or received != filing.strftime("%Y%m%d")):
            raise FinancialFeatureError("receipt_publication_day_mismatch")
        if not date(year, 12, 31) < filing < cutoff:
            raise FinancialFeatureError("not_publicly_available_by_prior_date")
        accounts = row.get("accounts")
        if not isinstance(accounts, dict) or set(accounts) - set(FINANCIAL_FIELDS):
            raise FinancialFeatureError("unknown_or_invalid_annual_accounts")
        values = {field: _money(accounts.get(field), field=field)
                  for field in FINANCIAL_FIELDS}
        assets, debt, equity = (
            values["assets_krw"], values["liabilities_krw"],
            values["equity_krw"])
        if all(v is not None for v in (assets, debt, equity)) and (
                abs(assets - debt - equity) > max(1, abs(assets) * 0.001)):
            raise FinancialFeatureError("balance_sheet_identity_inconsistent")
        if (values["interest_bearing_debt_krw"] is not None
                and debt is not None
                and values["interest_bearing_debt_krw"] > debt):
            raise FinancialFeatureError("interest_bearing_debt_exceeds_liabilities")
        records.append((year, values))
    if len(scopes) != 1:
        raise FinancialFeatureError("mixed_cfs_ofs_history")
    records.sort(key=lambda item: item[0])
    if [y for y, _ in records] != list(
            range(records[0][0], records[-1][0] + 1)):
        raise FinancialFeatureError("nonconsecutive_fiscal_years")
    trailing = [accounts for _, accounts in records[-3:]]
    first = records[-4][1]
    latest = records[-1][1]
    # Fully observed and positive sales in ALL four years are necessary.
    four_year_sales = [v["revenue_krw"] for _, v in records[-4:]]
    cagr = None
    if all(x is not None and x > 0 for x in four_year_sales):
        cagr = round(
            100 * ((four_year_sales[-1] / four_year_sales[0])**(1/3)-1), 6)
    margins = [_ratio(v["operating_income_krw"], v["revenue_krw"], scale=100)
               for v in trailing]
    fcf_ratios = []
    for v in trailing:
        cf, cap = v["operating_cashflow_krw"], v["capex_cash_outflow_krw"]
        fcf = cf-cap if cf is not None and cap is not None else None
        fcf_ratios.append(_ratio(fcf, v["assets_krw"], scale=100))
    avg_margin = round(sum(margins) / 3, 6) if all(
        v is not None for v in margins) else None
    avg_fcf_assets = round(sum(fcf_ratios) / 3, 6) if all(
        v is not None for v in fcf_ratios) else None
    net_debt_pct = None
    if (latest["interest_bearing_debt_krw"] is not None
            and latest["cash_krw"] is not None):
        net_debt_pct = _ratio(
            latest["interest_bearing_debt_krw"] - latest["cash_krw"],
            latest["equity_krw"], scale=100)
    cap = None
    if market_cap is not None:
        if (not isinstance(market_cap, dict)
                or market_cap.get("asof") != asof
                or type(market_cap.get("amount_krw")) is not int
                or not 0 < market_cap["amount_krw"] <= 10**19):
            raise FinancialFeatureError("market_cap_not_same_day_positive_krw")
        cap = market_cap["amount_krw"]
    cf, capex = latest["operating_cashflow_krw"], latest["capex_cash_outflow_krw"]
    fcf = cf - capex if cf is not None and capex is not None else None
    out = {
        "financial_revenue_cagr_3y_pct": cagr,
        "financial_operating_margin_3y_pct": avg_margin,
        "financial_fcf_assets_3y_pct": avg_fcf_assets,
        "financial_net_debt_equity_pct": net_debt_pct,
        "valuation_pbr": _ratio(cap, latest["owners_equity_krw"]),
        "valuation_earnings_yield_pct": _ratio(
            latest["owners_profit_krw"], cap, scale=100),
        "valuation_fcf_yield_pct": _ratio(fcf, cap, scale=100),
    }
    flags = []
    if latest["equity_krw"] is not None and latest["equity_krw"] <= 0:
        flags.append("negative_or_zero_equity")
    if latest["owners_profit_krw"] is not None and latest["owners_profit_krw"] < 0:
        flags.append("negative_owners_profit")
    if fcf is not None and fcf < 0:
        flags.append("negative_latest_free_cash_flow")
    if net_debt_pct is not None and net_debt_pct > 100:
        flags.append("net_debt_above_equity")
    return {
        "schema": "v3-annual-krw-financial-raw-features-0.1",
        "asof": asof, "issuer_id": issuer_id,
        "annual_years_observed": [y for y, _ in records],
        "accounting_scope": next(iter(scopes)),
        "features": out,
        "missing_feature_names": [k for k, v in out.items() if v is None],
        "financial_red_flag_candidates": flags,
        "receipt_and_sha_strings_authenticated": False,
        "revision_history_pit_certified": False,
        "market_cap_source_verified": False,
        "fcf_is_operating_cashflow_minus_capex_outflow": True,
        "full_v3_scoring_authorized": False,
        "confirmed_buy": False,
    }
