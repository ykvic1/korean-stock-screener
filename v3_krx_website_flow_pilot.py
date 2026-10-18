"""Research-only KRX Data Marketplace investor-flow feasibility pilot.

This is NOT the free KRX OPEN API, and is NOT a certified data feed.
pykrx reads the public KRX Data Marketplace investor-trading pages.
Never treat a blank/blocked response as zero investor demand. No retries,
trading, secrets, raw figures in console output, website publication,
full-market crawling, or production score changes.

One explicit stock; at most two pykrx calls on opt-in --live.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from numbers import Integral
import operator
import re
from zoneinfo import ZoneInfo

CODE = re.compile(r"[0-9]{6}\Z")
REQUIRED = ("기관합계", "외국인합계")
SUBJECTS = ("연기금 등", "연기금등", "연기금")
MAX_ABS = 10**13


class PortalFlowError(ValueError):
    """Only safe, fixed diagnostic labels; no HTTP error details."""


def check_day(value: str) -> date:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PortalFlowError("invalid_asof_date")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise PortalFlowError("invalid_asof_date") from None
    if day > datetime.now(ZoneInfo("Asia/Seoul")).date():
        raise PortalFlowError("future_asof_date")
    return day


def signed(value) -> int:
    """pykrx dataframe signed integer, never inferred or coerced from float."""
    if isinstance(value, bool):
        raise PortalFlowError("bad_signed_quantity")
    try:
        n = operator.index(value)
    except (TypeError, ValueError):
        raise PortalFlowError("bad_signed_quantity") from None
    if abs(n) > MAX_ABS:
        raise PortalFlowError("unreasonable_signed_quantity")
    return n


def _index_date(index) -> date:
    if type(index) is date:
        return index
    if isinstance(index, datetime):
        return index.date()
    to_date = getattr(index, "date", None)
    if callable(to_date):
        try:
            d = to_date()
        except Exception:
            raise PortalFlowError("invalid_market_day") from None
        if type(d) is date:
            return d
    raise PortalFlowError("invalid_market_day")


def summarize_daily(frame, *, code: str, asof: str, days: int = 20) -> dict:
    """Strict latest n date/quantity observations; no official session claim."""
    if type(code) is not str or not CODE.fullmatch(code) or days != 20:
        raise PortalFlowError("invalid_probe_identity")
    cutoff = check_day(asof)
    if (frame is None or not hasattr(frame, "columns") or
            not all(k in frame.columns for k in REQUIRED) or
            not hasattr(frame, "iterrows")):
        raise PortalFlowError("investor_daily_rows_unavailable")
    try:
        rows_count = len(frame)
    except Exception:
        raise PortalFlowError("invalid_daily_frame") from None
    if not 20 <= rows_count <= 60:
        raise PortalFlowError("insufficient_or_oversized_daily_history")
    observed = {}
    try:
        for index, row in frame.iterrows():
            day = _index_date(index)
            if day > cutoff or day in observed:
                raise PortalFlowError("future_or_duplicate_market_day")
            observed[day] = (signed(row[REQUIRED[0]]), signed(row[REQUIRED[1]]))
    except PortalFlowError:
        raise
    except Exception:
        raise PortalFlowError("invalid_daily_investor_values") from None
    if len(observed) != rows_count:
        raise PortalFlowError("mismatched_daily_row_count")
    ordered = sorted(observed, reverse=True)
    if (len(ordered) < days or
            (cutoff - ordered[0]).days > 5 or
            (ordered[0] - ordered[days - 1]).days > 45):
        raise PortalFlowError("stale_or_incomplete_latest_20")
    # Return metadata ONLY. Never expose signed quantities from this pilot.
    return {
        "status": "research_only_krx_portal_daily_candidate",
        "source": "KRX Data Marketplace via pykrx; NOT KRX Open API",
        "code": code,
        "asof": cutoff.isoformat(),
        "reported_recent_days": days,
        "observed_first_day": ordered[0].isoformat(),
        "observed_twentieth_day": ordered[days - 1].isoformat(),
        "institution_signed_quantity_format_valid": True,
        "foreign_signed_quantity_format_valid": True,
        "market_calendar_verified": False,
        "market_session_verified": False,
        "reliable_github_hosted_runner_access_verified": False,
        "data_redistribution_authorized": False,
        "confirmed_scoring_allowed": False,
        "ias_flow_component": None,
        "v3_release_ready": False,
    }


def check_pension_one_day(frame) -> bool:
    """Validate presence of separate period-total pension row, NOT daily history."""
    if frame is None or not hasattr(frame, "columns") or "순매수" not in frame.columns:
        return False
    if not hasattr(frame, "index") or not hasattr(frame, "loc"):
        return False
    try:
        names = [str(x).strip() for x in frame.index]
        matching = [x for x in names if x in SUBJECTS]
        if len(matching) != 1:
            return False
        signed(frame.loc[matching[0], "순매수"])
        return True
    except Exception:
        return False


def probe(*, code: str, asof: str, volume_by_date, volume_by_investor) -> dict:
    """Injectable source functions; exactly one daily and optional one pension query."""
    if type(code) is not str or not CODE.fullmatch(code):
        raise PortalFlowError("invalid_stock_code")
    cutoff = check_day(asof)
    start = (cutoff - timedelta(days=45)).strftime("%Y%m%d")
    end = cutoff.strftime("%Y%m%d")
    try:
        frame = volume_by_date(start, end, code)
    except Exception:
        raise PortalFlowError("krx_portal_daily_request_failed") from None
    summary = summarize_daily(frame, code=code, asof=asof)
    try:
        pension = volume_by_investor(end, end, code)
        summary["single_day_pension_etc_row_observed"] = check_pension_one_day(pension)
        summary["single_day_pension_query_status"] = (
            "observed_candidate" if summary["single_day_pension_etc_row_observed"]
            else "unavailable_or_changed_schema"
        )
    except Exception:
        summary["single_day_pension_etc_row_observed"] = False
        summary["single_day_pension_query_status"] = "provider_request_failed"
    summary["pension_20_60_120_250_history_verified"] = False
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KRX website investor-flow bounded research; not Open API")
    parser.add_argument("--code", default="252990")
    parser.add_argument("--asof", default="2026-10-16")
    parser.add_argument("--live", action="store_true",
                        help="Explicit one-stock, two-request KRX website observation")
    args = parser.parse_args(argv)
    if not args.live:
        print('{"status":"not_run","cause":"explicit_live_required"}')
        return 0
    try:
        from pykrx import stock
        report = probe(
            code=args.code, asof=args.asof,
            volume_by_date=stock.get_market_trading_volume_by_date,
            volume_by_investor=stock.get_market_trading_volume_by_investor,
        )
    except PortalFlowError as exc:
        print(json.dumps({"status": "research_unavailable", "cause": str(exc)}))
        return 1
    except Exception:
        print('{"status":"research_unavailable","cause":"unexpected_safe_error"}')
        return 1
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
