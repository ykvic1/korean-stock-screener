"""Reconcile the saved 2025-11-13 delisting pilot; never certify performance."""
import csv
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
from statistics import median

from v3_forward_horizon_readiness import HORIZONS, _anniversary
from v3_krx_regular_close import KST, regular_close_utc

ROOT = Path(__file__).resolve().parent
SIGNAL = date(2025, 11, 13)
SIGNAL_AT = regular_close_utc(SIGNAL).astimezone(KST).isoformat()
ANNUAL_DATES = ("2022-12-31", "2023-12-31", "2024-12-31")
ENTRY = date(2025, 11, 14)
# The Friday entry settles two KRX sessions later.
ENTRY_SETTLEMENT = date(2025, 11, 18)
TAG = "20251113"
UNPRICED = "awaiting_settlement_unpriced"
UNRESOLVED_DISTRIBUTION = "awaiting_final_distribution_unpriced"
UNPRICED_METHODS = {UNPRICED, UNRESOLVED_DISTRIBUTION}
CASH = {"cash_delivery": "reported_completed", "scheduled_cash_entitlement": "announced"}
LIQUIDATION = {"liquidation_close_scenario", "last_liquidation_close_then_cash"}
TOUCH_STATES = ("touch_observed", "no_touch_observed", "no_touch_observed_partial_path")
CLOSE = {"listed_close", "adjusted_listed_close"}
TOUCH = "touch_observed"


class PilotAuditError(ValueError):
    """A fixed diagnostic for the read-only watch."""


def require(condition, diagnostic):
    if not condition:
        raise PilotAuditError(diagnostic)


def day(value):
    parsed = date.fromisoformat(value)
    require(parsed.isoformat() == value, "invalid_session")
    return parsed


def positive(value):
    number = Decimal(value)
    require(number.is_finite() and number > 0, "invalid_positive_value")
    return number


def holding_units(adjustments, symbol, after, through):
    units = Decimal(1)
    for effective, factor in adjustments.get(symbol, []):
        if after < effective <= through:
            units *= factor
    return units


def exchanged_units(adjustments, code, settlement, through):
    event = day(settlement["event_on"])
    return (holding_units(adjustments, code, ENTRY, event)
            * positive(settlement["shares_per_old_share"])
            * holding_units(adjustments, settlement["successor_code"], event, through))


def dividend_units(code, payer, record, settlement, adjustments):
    """Apply the public terms to a continuous proportional holding."""
    successor = settlement["successor_code"]
    require(payer == code or (successor and payer == successor), "dividend_payer_mismatch")
    if record < ENTRY_SETTLEMENT:
        return Decimal(0)

    event = settlement["event_on"]
    if payer == code:
        if event and record > day(event):
            return Decimal(0)
        if event and record == day(event):
            return None
        if settlement["treatment"] == "liquidation_trading":
            if record >= day(settlement["kind_delisted_on"]):
                return Decimal(0)
            if record >= day(settlement["liquidation_last_on"]):
                return None
        return holding_units(adjustments, code, ENTRY, record)

    if record < day(event):
        return Decimal(0)
    # A record date between legal exchange and trading availability needs
    # the allotment/record-date evidence for that particular exchange.
    if record <= day(settlement["settlement_or_share_availability_on"]):
        return None
    return exchanged_units(adjustments, code, settlement, record)


def dividend_events(rows, references, settlements, adjustments):
    result = defaultdict(list)
    dispositions = Counter()
    for (code, payer, record_on), row in rows.items():
        record, payment = day(record_on), day(row["payment_on"])
        status = row["payment_evidence"]
        require(code in references and record < payment
                and row["terms_url"].startswith("https://")
                and ((status == "reported_in_payment_history" and row["payment_url"].startswith("https://"))
                     or (status == "scheduled" and not row["payment_url"])),
                "invalid_dividend_evidence")
        amount = positive(row["krw_per_payer_share"])
        units = dividend_units(code, payer, record, settlements[code], adjustments)
        if units is None:
            dispositions["ownership_unverified"] += 1
        elif units == 0:
            dispositions["outside_holding"] += 1
        else:
            dispositions["eligible"] += 1
            result[code].append((record, payment, status, amount * units))
    return result, dispositions


def cash_dividend_replay(price, events, valuation, entry):
    paid = Decimal(0)
    awaiting = 0
    for record, payment, status, amount in events:
        if record > valuation:
            continue
        if payment <= valuation and status == "reported_in_payment_history":
            paid += amount
        else:
            awaiting += 1
    result = {"entitled_cash_unverified_at_evaluation": awaiting}
    if not paid:
        return result
    result["reported_cash_krw_per_entry_share"] = str(
        paid.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    # A missing terminal value cannot turn into a cash-only performance row.
    if not price["value_per_entry_share_krw"]:
        return result
    # Recompute from the retained units and quote, before the stored display rounding.
    terminal = positive(price["units_per_entry_share"]) * positive(price["quote_or_cash_krw"])
    gross_return = ((terminal + paid) / entry - 1) * 100
    result["price_scenario_plus_reported_cash_pct"] = str(
        gross_return.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return result


def fractional_cash_terms(rows, prior_rows, exchange_codes, settlements, adjustments, through):
    require(prior_rows.keys() <= rows.keys(), "fractional_cash_prior_without_exchange")
    terms = {}
    for code, row in rows.items():
        require(code in exchange_codes, "fractional_cash_outside_stock_exchange_pilot")
        settlement = settlements[code]
        event = day(settlement["event_on"])
        listing = day(settlement["settlement_or_share_availability_on"])
        successor = settlement["successor_code"]
        prior = prior_rows.get(code)
        earlier = [(effective, ratio) for effective, ratio in adjustments.get(code, ())
                   if ENTRY < effective <= event]
        # A simultaneous or later successor adjustment requires separate settlement terms.
        require(len(earlier) == int(prior is not None)
                and not any(event <= effective <= through
                            for effective, _ in adjustments.get(successor, ())),
                "fractional_cash_requires_sequential_settlement")
        stages = []
        if prior:
            effective, ratio = earlier[0]
            require(day(prior["close_on"]) == effective < event and 0 < ratio < 1,
                    "invalid_pre_exchange_fractional_cash")
            stages.append(fractional_cash_stage(prior, code, ratio))
        reference_dates = {"listing_close": listing, "exchange_close": event}
        close_on = day(row["close_on"])
        require(reference_dates.get(row["close_basis"]) == close_on and event <= close_on <= listing,
                "invalid_fractional_cash_evidence")
        stages.append(fractional_cash_stage(row, successor, positive(settlement["shares_per_old_share"])))
        terms[code] = stages
    return terms


def fractional_cash_stage(row, quote_code, ratio):
    require(row["quote_code"] == quote_code and int(row["observed_volume"]) > 0
            and row["terms_url"].startswith("https://") and row["price_source_url"].startswith("https://"),
            "invalid_fractional_cash_evidence")
    return day(row["close_on"]), positive(row["close_krw"]), ratio


def fractional_cash_replay(price, terms, valuation, entry):
    method = price["method"]
    if method == "stock_delivery_mark":
        stages = terms
        shares_field = "successor_whole_shares"
    elif method == "adjusted_listed_close" and len(terms) > 1:
        stages = terms[:-1]
        shares_field = "original_whole_shares"
    else:
        return {}
    if valuation < stages[-1][0]:
        return {}
    quote = positive(price["quote_or_cash_krw"])
    require(valuation != stages[-1][0] or quote == stages[-1][1], "fractional_cash_quote_mismatch")
    replay = {}
    for entry_shares in (1, 100):
        whole = entry_shares
        cash_claim = Decimal(0)
        for _, close, ratio in stages:
            allotted = whole * ratio
            whole = int(allotted)
            cash_claim += (allotted - whole) * close
        gross_return = ((whole * quote + cash_claim) / (entry * entry_shares) - 1) * 100
        replay[str(entry_shares)] = {
            shares_field: whole,
            "cash_claim_krw": str(cash_claim.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "price_plus_cash_claim_pct": str(
                gross_return.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        }
    return replay


def check_price(price, settlement, last_trade, adjustments, valuation):
    method = price["method"]
    code = price["code"]
    observed_code = price["observed_code"]
    treatment = settlement["treatment"]
    event = settlement["event_on"]
    available = settlement["settlement_or_share_availability_on"]

    if method in UNPRICED_METHODS:
        require(not any(price[field] for field in (
            "observation_on", "observed_code", "units_per_entry_share", "quote_or_cash_krw",
            "value_per_entry_share_krw", "price_change_pct",
        )), "invalid_unpriced_settlement")
        if method == UNPRICED:
            # Trading can halt before the exchange takes legal effect.
            require(treatment == "stock_exchange" and day(last_trade) < valuation < day(available),
                    "invalid_unpriced_settlement")
        else:
            require(treatment == "distribution_unresolved" and day(last_trade) < valuation
                    and not available and not settlement["cash_krw_per_old_share"],
                    "invalid_unpriced_distribution")
        return

    observed = day(price["observation_on"])
    units = positive(price["units_per_entry_share"])
    quote = positive(price["quote_or_cash_krw"])
    value = units * quote
    price_change = (value / positive(price["entry_open_krw"]) - 1) * 100
    require(ENTRY <= observed <= valuation, "observation_outside_horizon")
    require(abs(positive(price["value_per_entry_share_krw"]) - value) <= Decimal("0.00005")
            and abs(Decimal(price["price_change_pct"]) - price_change) <= Decimal("0.005"),
            "value_or_return_mismatch")

    if method in CLOSE:
        expected = holding_units(adjustments, code, ENTRY, observed)
        require(observed == valuation and observed_code == code
                and (not event or observed < day(event))
                and observed < day(settlement["kind_delisted_on"]) and observed <= day(last_trade)
                and units == expected and (method == "listed_close") == (units == 1),
                "listed_close_mismatch")
    elif method == "stock_delivery_mark":
        require(treatment == "stock_exchange" and observed_code == settlement["successor_code"]
                and observed == valuation and day(event) <= day(available) <= observed,
                "stock_delivery_date_or_identity_mismatch")
        expected = exchanged_units(adjustments, code, settlement, observed)
        require(units == expected, "stock_delivery_ratio_mismatch")
    elif method in CASH:
        require(treatment == "cash_exchange" and not observed_code
                and observed == day(available) and day(event) <= observed
                and units == holding_units(adjustments, code, ENTRY, day(event))
                and quote == positive(settlement["cash_krw_per_old_share"])
                and settlement["settlement_date_evidence"] == CASH[method],
                "cash_payment_evidence_mismatch")
    elif method in LIQUIDATION:
        require(treatment == "liquidation_trading" and observed_code == code
                and observed < day(settlement["kind_delisted_on"]) <= valuation
                and price["observation_on"] == last_trade == settlement["liquidation_last_on"]
                and units == holding_units(adjustments, code, ENTRY, observed), "liquidation_scenario_mismatch")
    else:
        raise PilotAuditError("unknown_price_method")


def check_path(path, reference, valuation, previous):
    eligible, bars, gaps = (int(path[field]) for field in (
        "eligible_sessions", "observed_trade_bars", "unpriced_sessions",
    ))
    require(bars > 0 and gaps >= 0 and bars + gaps == eligible
            and bars >= previous[0] and gaps >= previous[1], "invalid_path_session_counts")
    first_touch = reference["first_observed_touch_on"]
    observed_touch = first_touch and day(first_touch) <= valuation
    status = (TOUCH if observed_touch else
              "no_touch_observed_partial_path" if gaps else "no_touch_observed")
    require(path["touch_status"] == status
            and path["first_observed_touch_on"] == (first_touch if observed_touch else ""),
            "reference_touch_mismatch")

    adverse = Decimal(path["maximum_adverse_excursion_observed_pct"])
    favorable = Decimal(path["maximum_favorable_excursion_observed_pct"])
    require(0 <= adverse < 100 and adverse >= previous[2] and favorable >= previous[3]
            and ENTRY <= day(path["adverse_extreme_on"]) <= valuation
            and ENTRY <= day(path["favorable_extreme_on"]) <= valuation, "invalid_path_extremes")
    return bars, gaps, adverse, favorable


def successor_listing_counts(rows, exchanges, settlements, share_actions):
    require(rows.keys() == exchanges, "successor_listing_coverage_mismatch")
    counts = Counter()
    for code, row in rows.items():
        term = settlements[code]
        successor = row["successor_code"]
        listing = row["listing_on"]
        before, after, issued = (int(row[key]) for key in ("shares_before", "shares_after", "reported_new_shares"))
        cancelled = int(row["cancelled_before_consolidation"])
        mode = row["treatment"]
        require(successor == term["successor_code"] and listing == term["settlement_or_share_availability_on"]
                and ENTRY <= day(row["prior_trade_on"]) < day(listing)
                and min(before, after) > 0 and row["source_url"].startswith("https://"),
                "invalid_successor_listing_identity")
        if mode == "new_shares":
            require(issued > 0 and cancelled == 0 and before + issued == after, "successor_issuance_count_mismatch")
        elif mode == "treasury_delivery":
            require(issued == 0 and cancelled == 0 and before == after, "successor_treasury_count_mismatch")
        elif mode == "merger_then_consolidation":
            action = share_actions.get((successor, listing))
            require(issued > 0 and action and row["prior_trade_on"] == action["prior_trade_on"]
                    and before == int(action["prior_trade_listed_shares"])
                    and after == int(action["shares_after_action"])
                    and cancelled > 0 and before + issued - cancelled == int(action["shares_before_action"]),
                    "successor_consolidation_count_mismatch")
        else:
            raise PilotAuditError("invalid_successor_listing_treatment")
        counts[mode] += 1
    return dict(counts)


def filing_date(receipt):
    require(re.fullmatch(r"[0-9]{14}", receipt), "invalid_replay_receipt")
    return day(f"{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}")


def statement_metrics(statements):
    margins = []
    leverage = None
    for period in ANNUAL_DATES:
        row = statements.get(period)
        if row is None:
            continue
        assets, liabilities, equity, revenue, earnings = (
            int(row[field]) if row[field] else None for field in
            ("assets_krw", "liabilities_krw", "equity_krw", "revenue_krw", "operating_income_krw")
        )
        balance = (assets, liabilities, equity)
        require(all(v is None for v in balance) or (
            all(v is not None for v in balance) and assets > 0
            and liabilities >= 0 and assets == liabilities + equity
        ), "invalid_replay_statement_balance")
        if assets is not None and period == ANNUAL_DATES[-1]:
            leverage = Decimal(liabilities) / assets * 100
        if revenue is not None and revenue > 0 and earnings is not None:
            margins.append(Decimal(earnings) / revenue * 100)
    return leverage, sum(margins) / len(ANNUAL_DATES) if len(margins) == len(ANNUAL_DATES) else None


def audited_financial_inputs(references, roster, trades, coverage, filings, prior, annual):
    grouped = defaultdict(dict)
    prior_codes = {code for code, _ in prior}
    selected = {}
    for (code, receipt), filing in filings.items():
        published = filing_date(receipt)
        status = filing["status"]
        require(code in references and references[code]["name"] == filing["name"]
                and filing["signal_at_kst"] == SIGNAL_AT and day(filing["published_on_kst"]) == published
                and day(filing["period_end"]) <= published and published != SIGNAL
                and status in {"excluded_after_cutoff", "selected_periodic", "superseded_before_cutoff",
                               "available_attachment", "available_preliminary"}
                and (published > SIGNAL) == (status == "excluded_after_cutoff"),
                "invalid_replay_filing_time")
        if status == "selected_periodic":
            period = filing["period_end"]
            require((code, period) not in selected, "duplicate_replay_period_receipt")
            selected[code, period] = receipt

    for (code, receipt), filing in filings.items():
        previous_receipt = filing["supersedes_receipt"]
        if previous_receipt:
            previous = filings.get((code, previous_receipt))
            require(previous and previous["period_end"] == filing["period_end"]
                    and previous_receipt < receipt, "invalid_replay_filing_revision")
    for (code, period), row in prior.items():
        receipt = row["dart_receipt"]
        require(selected.get((code, row["filing_period_end"])) == receipt
                and row["signal_at_kst"] == SIGNAL_AT and period <= row["filing_period_end"],
                "unselected_replay_statement_receipt")
        if row["filing_period_end"] == ANNUAL_DATES[-1]:
            grouped[code][period] = row
    for (code, period), row in annual.items():
        if code in references:
            require(code not in prior_codes, "overlapping_replay_annual_ledgers")
            require(row["market"] == roster[code]["market"], "replay_financial_identity_mismatch")
            grouped[code][period] = row

    inputs = {}
    for code, reference in references.items():
        summary = coverage[code]
        statements = grouped[code]
        receipt = summary["selected_fy2024_receipt"]
        require(summary["name"] == reference["name"] == roster[code]["anchor_name"]
                and summary["market"] == roster[code]["market"]
                and summary["later_delisted_on"] == roster[code]["delisted_on"]
                and summary["next_session_observation"] == trades[code]["next_session_status"],
                "replay_financial_identity_mismatch")
        if not statements:
            require(summary["review_status"] == "fund_distribution_instrument" and not receipt
                    and not int(summary["annual_statement_rows"]), "missing_replay_annual_statements")
            continue
        require(filing_date(receipt) < SIGNAL and ANNUAL_DATES[-1] in statements
                and statements.keys() <= set(ANNUAL_DATES)
                and len(statements) == int(summary["annual_statement_rows"])
                and summary["review_status"] in {"reviewed_prior_pilot", "reviewed_fy2024"},
                "invalid_replay_annual_selection")
        if summary["review_status"] == "reviewed_fy2024":
            versions = {item: filing_date(item) for item in summary["identified_version_receipts"].split("|")}
            eligible = [item for item, published in versions.items() if published < SIGNAL]
            require(eligible and SIGNAL not in versions.values() and receipt == max(eligible),
                    "invalid_replay_annual_version")
        scopes = {row["scope"] for row in statements.values()}
        require(len(scopes) == 1 and scopes <= {"CFS", "OFS"}
                and all(row["dart_receipt"] == receipt and row["name"] == reference["name"]
                        and row["statement_url"].startswith("https://")
                        and re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"])
                        for row in statements.values()), "invalid_replay_annual_source")
        metrics = statement_metrics(statements)
        for key, actual in zip(("fy2024_liabilities_to_assets_pct", "three_year_operating_margin_pct"), metrics):
            saved = summary[key]
            require((actual is None and not saved) or (actual is not None and saved
                    and abs(Decimal(saved) - actual) <= Decimal("0.0000005001")),
                    "replay_annual_metric_mismatch")
        inputs[code] = {"annual_receipt": receipt, "scope": next(iter(scopes)),
                        "liabilities_to_assets_pct": summary["fy2024_liabilities_to_assets_pct"] or None,
                        "mean_operating_margin_pct": summary["three_year_operating_margin_pct"] or None}
    return inputs


def financial_horizon_summary(replays, horizon):
    rows = [replay[horizon] for replay in replays.values()]
    returns = [Decimal(row["price_change_pct"]) for row in rows if row["price_change_pct"] is not None]
    adverse = [Decimal(row["maximum_adverse_excursion_observed_pct"]) for row in rows]
    step = Decimal("0.01")
    return {"with_price_evaluation": len(returns), "with_observed_path": len(adverse),
            "with_reference_touch": sum(row["reference_touch"] for row in rows),
            "with_path_gaps": sum(row["path_has_gaps"] for row in rows),
            "median_price_change_pct": (
                str(median(returns).quantize(step, rounding=ROUND_HALF_UP)) if returns else None),
            "median_observed_adverse_excursion_pct": (
                str(median(adverse).quantize(step, rounding=ROUND_HALF_UP)) if adverse else None)}


def audit(root=ROOT, *, daily_replay=None):
    hashes = {}

    def load(ledger, *keys):
        raw = (root / "ops" / f"v3-performance-{ledger}-{TAG}.csv").read_bytes()
        hashes[ledger] = sha256(raw).hexdigest()
        reader = csv.DictReader(StringIO(raw.decode("utf-8"), newline=""), strict=True)
        rows = {}
        for row in reader:
            require(None not in row and None not in row.values(), "invalid_pilot_csv_row")
            key = tuple(row[field] for field in keys) if len(keys) > 1 else row[keys[0]]
            require(key not in rows, "duplicate_pilot_key")
            rows[key] = row
        require(bool(rows), "empty_pilot_ledger")
        return rows

    references = load("downside-reference", "code")
    prices = load("price-replay", "code", "horizon_months")
    paths = load("downside-horizons", "code", "horizon_months")
    roster = load("delistings", "code")
    trades = load("delisting-trade-observations", "code")
    settlements = load("delisting-settlement", "code")
    adjustments = defaultdict(list)
    share_actions = load("share-adjustments", "code", "listing_on")
    for (code, listing_on), row in share_actions.items():
        listed = day(listing_on)
        before_trade = day(row["prior_trade_on"])
        new, old, traded, before, after = (int(row[key]) for key in (
            "new_units", "old_units", "prior_trade_listed_shares", "shares_before_action", "shares_after_action",
        ))
        require(min(new, old, before, after, traded) > 0 and new != old
                and ENTRY <= before_trade < listed and row["source_url"].startswith("https://")
                and (before * new) // old == after, "invalid_share_adjustment_evidence")
        intervening = row["intervening_action_on"]
        if traded != before:
            require(intervening and ENTRY < day(intervening) < listed
                    and row["intervening_action_url"].startswith("https://"),
                    "unexplained_share_count_transition")
        else:
            require(not intervening and not row["intervening_action_url"],
                    "unexpected_intervening_share_action")
        adjustments[code].append((listed, Decimal(new) / old))

    members = {code for code, row in roster.items() if row["anchor_membership"] == "present"}
    require(references.keys() <= members and settlements.keys() <= members
            and trades.keys() == members, "pilot_membership_mismatch")
    expected_keys = {(code, str(months)) for code in references for months in HORIZONS}
    require(prices.keys() == paths.keys() == expected_keys, "pilot_horizon_join_mismatch")
    for code, settlement in settlements.items():
        require(settlement["kind_delisted_on"] == roster[code]["delisted_on"]
                and settlement["market"] == roster[code]["market"]
                and settlement["term_source_url"].startswith("https://"),
                "settlement_roster_mismatch")
        if settlement["treatment"] == "liquidation_trading":
            start = day(settlement["liquidation_first_on"])
            end = day(settlement["liquidation_last_on"])
            require(start <= end < day(settlement["kind_delisted_on"])
                    and trades[code]["last_positive_volume_candidate"] == end.isoformat(),
                    "liquidation_notice_date_mismatch")

    financial_inputs = audited_financial_inputs(
        references, roster, trades, load("delisted-financial-coverage", "code"),
        load("filing-availability", "code", "dart_receipt"),
        load("financial-statements", "code", "statement_period_end"),
        load("delisted-annual-statements", "code", "statement_period_end"),
    )
    financial_replays = {code: {} for code in financial_inputs}
    dividend_rows = load("cash-dividends", "position_code", "payer_code", "record_on")
    events, dividend_dispositions = dividend_events(dividend_rows, references, settlements, adjustments)
    dividend_replays = {}
    exchanges = {code for code in references if settlements[code]["treatment"] == "stock_exchange"}
    successor_listings = load("successor-listings", "code")
    ledger_exchanges = {code for code, row in settlements.items() if row["treatment"] == "stock_exchange"}
    successor_counts = successor_listing_counts(successor_listings, ledger_exchanges, settlements, share_actions)
    end = max(day(row["valuation_session"]) for row in prices.values())
    fractions = fractional_cash_terms(
        load("fractional-cash", "code"), load("pre-exchange-fractional-cash", "code"),
        exchanges, settlements, adjustments, end,
    )
    fraction_replays = {}
    horizons = {str(months): Counter() for months in HORIZONS}
    methods = Counter()
    valuations = {}
    for code, reference in references.items():
        entry = positive(reference["entry_open_krw"])
        line = positive(reference["reference_line_krw"])
        require(day(reference["signal_session"]) == SIGNAL and day(reference["entry_session"]) == ENTRY
                and trades[code]["next_session_status"] == "traded", "invalid_pilot_entry")
        previous = (0, 0, Decimal(0), Decimal(0))
        previous_valuation = ENTRY
        for months in HORIZONS:
            horizon = str(months)
            price, path = prices[code, horizon], paths[code, horizon]
            valuation = day(price["valuation_session"])
            anniversary = _anniversary(SIGNAL, months)
            require(day(price["anniversary"]) == anniversary <= valuation
                    and valuation > previous_valuation
                    and valuations.setdefault(horizon, valuation) == valuation,
                    "pilot_valuation_session_mismatch")
            require(price["name"] == path["name"] == reference["name"]
                    and day(price["entry_session"]) == ENTRY and positive(price["entry_open_krw"]) == entry
                    and path["valuation_session"] == price["valuation_session"]
                    and path["terminal_price_status"] == price["method"], "pilot_identity_or_path_mismatch")
            check_price(price, settlements[code], trades[code]["last_positive_volume_candidate"],
                        adjustments, valuation)
            previous = check_path(path, reference, valuation, previous)
            previous_valuation = valuation
            methods[price["method"]] += 1
            counts = horizons[horizon]
            counts["with_value"] += price["method"] not in UNPRICED_METHODS
            counts["unpriced_valuations"] += price["method"] in UNPRICED_METHODS
            counts["unpriced_final_distributions"] += price["method"] == UNRESOLVED_DISTRIBUTION
            counts["scheduled_cash_entitlements"] += price["method"] == "scheduled_cash_entitlement"
            counts["liquidation_scenarios"] += price["method"] in LIQUIDATION
            counts["paths_with_unpriced_sessions"] += previous[1] > 0
            counts[path["touch_status"]] += 1
            if code in financial_inputs:
                financial_replays[code][horizon] = {
                    "price_change_pct": price["price_change_pct"] or None,
                    "price_method": price["method"],
                    "maximum_adverse_excursion_observed_pct": path["maximum_adverse_excursion_observed_pct"],
                    "reference_touch": path["touch_status"] == TOUCH,
                    "path_has_gaps": previous[1] > 0,
                }
            dividend = cash_dividend_replay(price, events.get(code, ()), valuation, entry)
            counts["price_scenarios_with_reported_dividend_cash"] += (
                "price_scenario_plus_reported_cash_pct" in dividend)
            counts["paths_with_dividend_entitlement_unpaid_or_unverified"] += (
                dividend["entitled_cash_unverified_at_evaluation"] > 0)
            if "reported_cash_krw_per_entry_share" in dividend or dividend["entitled_cash_unverified_at_evaluation"]:
                dividend_replays.setdefault(code, {})[horizon] = dividend
            fraction = fractional_cash_replay(price, fractions[code], valuation, entry) if code in fractions else {}
            counts["price_scenarios_with_fractional_cash_claim"] += bool(fraction)
            if fraction:
                fraction_replays.setdefault(code, {})[horizon] = fraction

        low = positive(reference["lowest_observed_value_krw"])
        require(ENTRY <= day(reference["lowest_observed_value_on"]) <= previous_valuation
                and abs((1 - low / entry) * 100 - previous[2]) <= Decimal("0.000051")
                and reference["lowest_observed_value_on"] == path["adverse_extreme_on"],
                "reference_low_mismatch")
        first_touch = reference["first_observed_touch_on"]
        require(bool(first_touch) == (low <= line), "reference_touch_threshold_mismatch")
        if first_touch:
            require(ENTRY <= day(first_touch) <= previous_valuation
                    and low <= positive(reference["first_touch_low_value_krw"]) <= line,
                    "reference_first_touch_invalid")

    non_spac = {code for code in members if roster[code]["kind_spac_name"] == "false"}
    non_spac_entry_traded = {code for code in non_spac if trades[code]["next_session_status"] == "traded"}
    replay_summary = (
        daily_replay(references, prices, paths, settlements, trades, adjustments, share_actions,
                     {code: successor_listings[code] for code in exchanges})
        if daily_replay else {}
    )
    return {
        "schema": "v3-historical-delisting-pilot-audit-1",
        **replay_summary,
        "signal_session": SIGNAL.isoformat(),
        "pilot_issuers": len(references),
        "pilot_horizon_rows": len(prices),
        "delisted_anchor_issuers": len(members),
        "delisted_anchor_non_spac": len(non_spac),
        "next_session_traded_non_spac": len(non_spac_entry_traded),
        "next_session_traded_non_spac_outside_pilot": len(non_spac_entry_traded - references.keys()),
        "settlement_register_issuers": len(settlements),
        "liquidation_notice_issuers": sum(
            row["treatment"] == "liquidation_trading" for row in settlements.values()
        ),
        "unresolved_distribution_register_issuers": sum(
            row["treatment"] == "distribution_unresolved" for row in settlements.values()
        ),
        "financial_replay_inputs": financial_inputs,
        "financial_replays": financial_replays,
        "financial_horizons": {str(h): financial_horizon_summary(financial_replays, str(h)) for h in HORIZONS},
        "methods": dict(methods),
        "dividend_event_rows": len(dividend_rows),
        "dividend_eligible_events": dividend_dispositions["eligible"],
        "dividend_events_outside_holding": dividend_dispositions["outside_holding"],
        "dividend_events_ownership_unverified": dividend_dispositions["ownership_unverified"],
        "dividend_replays": dividend_replays,
        "dividend_coverage_complete": False,
        "fractional_cash_terms_issuers": len(fractions),
        "fractional_cash_unmodeled_issuers": sorted(exchanges - fractions.keys()),
        "fractional_cash_replays": fraction_replays,
        "fractional_cash_payments_verified": False,
        "horizons": {h: {**dict.fromkeys(TOUCH_STATES, 0), **counts}
                     for h, counts in horizons.items()},
        "ledger_sha256": hashes,
        "successor_share_delivery_counts": successor_counts,
        "share_count_corroborated_actions": sum(map(len, adjustments.values())),
        "source_prices_independently_verified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
    }


def main():
    try:
        report = audit()
    except PilotAuditError as exc:
        raise SystemExit(f"Historical pilot audit failed: {exc}") from None
    except (OSError, UnicodeError, csv.Error, InvalidOperation, ValueError, KeyError):
        raise SystemExit("Historical pilot audit failed: unreadable or malformed evidence") from None
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
