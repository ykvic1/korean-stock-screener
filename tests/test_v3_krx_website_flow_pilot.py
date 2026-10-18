"""Offline-only KRX website investor flow feasibility tests; no source calls."""
from __future__ import annotations

from datetime import date, timedelta, datetime
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import v3_krx_website_flow_pilot as pilot


def weekdays(asof="2026-10-16", count=32):
    d = date.fromisoformat(asof)
    rows = []
    while len(rows) < count:
        if d.weekday() < 5:
            rows.append((d, {"기관합계": 200 if len(rows) % 2 else -100,
                             "외국인합계": -120 if len(rows) % 2 else 400}))
        d -= timedelta(days=1)
    return rows


class Frame:
    columns = ("기관합계", "외국인합계")
    def __init__(self, rows=None):
        self.rows = weekdays() if rows is None else rows
    def __len__(self):
        return len(self.rows)
    def iterrows(self):
        return iter(self.rows)


class PensionIndex:
    def __getitem__(self, key):
        if key == ("연기금 등", "순매수"):
            return -43
        raise KeyError("not found")


class PensionFrame:
    columns = ("매도", "매수", "순매수")
    index = ("금융투자", "보험", "투신", "연기금 등")
    loc = PensionIndex()


class KRXWebsiteFlowPilotTests(unittest.TestCase):
    def test_dry_run_makes_no_source_calls(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = pilot.main(["--asof", "2026-10-16"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "not_run")

    def test_strict_20_valid_rows_no_raw_values_emitted(self):
        x = pilot.summarize_daily(Frame(), code="252990", asof="2026-10-16")
        self.assertEqual(x["status"], "research_only_krx_portal_daily_candidate")
        self.assertEqual(x["reported_recent_days"], 20)
        self.assertEqual(x["observed_first_day"], "2026-10-16")
        self.assertFalse(x["confirmed_scoring_allowed"])
        self.assertFalse(x["market_calendar_verified"])
        self.assertNotIn("net_shares", json.dumps(x))
        self.assertNotIn("200", json.dumps(x))

    def test_probe_is_bounded_two_calls_without_keys_or_urls(self):
        calls = []
        def date_fetch(start,end,code):
            calls.append(("daily",start,end,code))
            return Frame()
        def by_investor(start,end,code):
            calls.append(("subjects",start,end,code))
            return PensionFrame()
        result = pilot.probe(code="252990",asof="2026-10-16",
                             volume_by_date=date_fetch,
                             volume_by_investor=by_investor)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][0],"daily")
        self.assertEqual(calls[1],("subjects","20261016","20261016","252990"))
        self.assertTrue(result["single_day_pension_etc_row_observed"])
        self.assertFalse(result["pension_20_60_120_250_history_verified"])
        self.assertFalse(result["v3_release_ready"])

    def test_invalid_and_future_date_fail(self):
        for bad in ("2026-02-30", "20261016", "2026-13-01", "2099-12-31", None):
            with self.subTest(bad=bad), self.assertRaises(pilot.PortalFlowError):
                pilot.check_day(bad)

    def test_quantity_requires_true_signed_integral(self):
        for bad in (True, False, 1.0, 1.2, "100", None, 10**14):
            with self.subTest(bad=bad), self.assertRaises(pilot.PortalFlowError):
                pilot.signed(bad)
        self.assertEqual(pilot.signed(-100), -100)
        self.assertEqual(pilot.signed(0), 0)

    def test_missing_columns_empty_or_partial_period_rejected(self):
        with self.assertRaisesRegex(pilot.PortalFlowError, "unavailable"):
            pilot.summarize_daily(None, code="252990", asof="2026-10-16")
        with self.assertRaisesRegex(pilot.PortalFlowError, "insufficient"):
            pilot.summarize_daily(Frame(weekdays(count=12)), code="252990",
                                  asof="2026-10-16")
        frame=Frame()
        frame.columns=("기관합계","기타법인")
        with self.assertRaisesRegex(pilot.PortalFlowError, "unavailable"):
            pilot.summarize_daily(frame, code="252990", asof="2026-10-16")

    def test_missing_one_integer_not_accepted_as_zero(self):
        rows=weekdays()
        rows[4]=(rows[4][0],{"기관합계": None,"외국인합계": 0})
        with self.assertRaisesRegex(pilot.PortalFlowError, "bad_signed_quantity"):
            pilot.summarize_daily(Frame(rows),code="252990",asof="2026-10-16")

    def test_duplicate_future_or_stale_dates_rejected(self):
        for mode in ("duplicate","future","stale"):
            rows=weekdays()
            if mode=="duplicate":
                rows[5]=(rows[4][0], rows[5][1])
            elif mode=="future":
                rows[0]=(date(2026,10,20), rows[0][1])
            else:
                rows=weekdays(asof="2026-09-30")
            with self.subTest(mode=mode), self.assertRaises(pilot.PortalFlowError):
                pilot.summarize_daily(Frame(rows),code="252990",asof="2026-10-16")

    def test_supplier_error_fail_closed_without_leaking_remote_message(self):
        def unavailable(*args):
            raise RuntimeError("remote secret 12345?account=1234")
        with self.assertRaises(pilot.PortalFlowError) as caught:
            pilot.probe(code="252990",asof="2026-10-16",
                        volume_by_date=unavailable,
                        volume_by_investor=unavailable)
        self.assertEqual(str(caught.exception), "krx_portal_daily_request_failed")

    def test_pension_schema_unavailable_keeps_primary_pilot_research_only(self):
        def missing(*args):
            raise RuntimeError("changed or blocked")
        report=pilot.probe(code="252990",asof="2026-10-16",
                           volume_by_date=lambda *x: Frame(),
                           volume_by_investor=missing)
        self.assertFalse(report["single_day_pension_etc_row_observed"])
        self.assertEqual(report["single_day_pension_query_status"],"provider_request_failed")
        self.assertFalse(report["confirmed_scoring_allowed"])

    def test_reject_wrong_stock_identity(self):
        with self.assertRaises(pilot.PortalFlowError):
            pilot.probe(code="00593",asof="2026-10-16",
                        volume_by_date=lambda *x:Frame(),
                        volume_by_investor=lambda *x:PensionFrame())


if __name__=="__main__":
    unittest.main()
