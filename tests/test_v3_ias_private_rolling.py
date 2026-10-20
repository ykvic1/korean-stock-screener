"""Synthetic tests for privacy-safe multiday Kiwoom IAS investor-flow source join."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, timedelta
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import v3_ias_private_rolling as m


def business_rows(ending="2026-10-16", count=250):
    day=date.fromisoformat(ending)
    rows=[]
    while len(rows)<count:
        if day.weekday()<5:
            rows.append({
                "trade_date": day.isoformat(),
                "inst_net_shares":20,"foreign_net_shares":10,
                "pension_etc_net_shares":5,
                "investment_trust_net_shares":0,
                "insurance_net_shares":0,
                "financial_investment_net_shares":0,
                "private_fund_net_shares":0,
            })
        day-=timedelta(days=1)
    return rows


def source(rows, *, asof="2026-10-16", code="252990"):
    return {
        "schema":m.SOURCE_SCHEMA,"provider":m.SOURCE,"asof":asof,
        "status":"complete","published":False,
        "confirmed_scoring_allowed":False,"v3_release_ready":False,
        "datasets":{code:{
            "code":code,"source":"Kiwoom REST ka10059 KRX-code request",
            "asof_requested":asof,
            "confirmed_scoring_allowed":False,"v3_release_ready":False,
            "independent_exchange_session_verified":False,
            "row_count":len(rows),
            "first_day":rows[0]["trade_date"],"last_day":rows[-1]["trade_date"],
            "rows":rows,
        }},
    }


class FlowPrivateRollingTests(unittest.TestCase):
    def test_genuine_250_distinct_observations_yield_four_complete_windows(self):
        report=m.combine_snapshots([source(business_rows())],asof="2026-10-16")
        self.assertEqual(report["distinct_stocks"],1)
        self.assertEqual(report["full_windows"]["250"]["pension_etc_net_shares"],1)
        self.assertEqual(report["ias_20_scored_count"],0)
        row=report["records"]["252990"]
        self.assertEqual(row["distinct_observed_dates"],250)
        for n in (20,60,120,250):
            view=row["periods"][str(n)]
            self.assertEqual(view["foreign_net_shares"],n*10)
            self.assertEqual(view["institution_total_net_shares"],n*20)
            self.assertEqual(view["pension_etc_net_shares"],n*5)
            self.assertTrue(view["pension_must_not_be_added_to_institution"])
            self.assertIsNone(view["point_score"])
        self.assertFalse(row["corporate_actions_adjusted"])
        self.assertFalse(row["trading_calendar_verified"])
        self.assertFalse(report["approved_for_v3_score"])

    def test_100_observations_never_impute_120_250_or_pension(self):
        payload=source(business_rows(count=100))
        for row in payload["datasets"]["252990"]["rows"]:
            row["pension_etc_net_shares"]=None
        r=m.combine_snapshots([payload],asof="2026-10-16")
        w=r["records"]["252990"]["periods"]
        self.assertEqual(w["20"]["institution_total_net_shares"],400)
        self.assertIsNone(w["20"]["pension_etc_net_shares"])
        self.assertIsNone(w["120"]["foreign_net_shares"])
        self.assertEqual(r["full_windows"]["250"]["foreign_net_shares"],0)

    def test_real_observed_zero_is_not_null(self):
        payload=source(business_rows(count=250))
        for row in payload["datasets"]["252990"]["rows"]:
            row["inst_net_shares"]=0
        r=m.combine_snapshots([payload],asof="2026-10-16")
        self.assertEqual(r["records"]["252990"]["periods"]["250"]
                         ["institution_total_net_shares"],0)
        self.assertEqual(r["full_windows"]["250"]["institution_total_net_shares"],1)

    def test_two_source_days_merge_unique_overlap_into_more_than_250_days(self):
        newest=source(business_rows("2026-10-16",100))
        older=source(business_rows("2026-09-18",250),asof="2026-09-18")
        got=m.combine_snapshots([older,newest],asof="2026-10-16")
        self.assertGreater(got["records"]["252990"]["distinct_observed_dates"],250)
        self.assertEqual(got["distinct_snapshot_days"],2)
        self.assertEqual(got["records"]["252990"]["periods"]["250"]
                         ["foreign_net_shares"],2500)
        self.assertEqual(got["full_windows"]["250"]["pension_etc_net_shares"],1)

    def test_conflicting_overlap_cannot_modify_original(self):
        newest=source(business_rows("2026-10-16",100))
        older=source(business_rows("2026-09-18",250),asof="2026-09-18")
        older_before=copy.deepcopy(older)
        older["datasets"]["252990"]["rows"][0]["inst_net_shares"]=999
        with self.assertRaisesRegex(m.PrivateRollingFlowError,
                                    "overlapping_provider_flow_revision_conflict"):
            m.combine_snapshots([older,newest],asof="2026-10-16")
        self.assertEqual(older_before["datasets"]["252990"]["rows"][1],
                         older["datasets"]["252990"]["rows"][1])

    def test_too_old_window_is_missing_not_negative_flow(self):
        r=m.combine_snapshots([source(business_rows())],
                              asof="2026-11-01",max_age_days=7)
        w=r["records"]["252990"]["periods"]["20"]
        self.assertIsNone(w["foreign_net_shares"])
        self.assertFalse(r["records"]["252990"]["freshness_candidate"])

    def test_missing_forged_or_invalid_data_fails_closed(self):
        baseline=source(business_rows(count=25))
        cases=[]
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["rows"][0]["foreign_net_shares"]=True;cases.append(t)
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["rows"][0]["pension_etc_net_shares"]=1.4;cases.append(t)
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["rows"][0]["trade_date"]="2026-11-01";cases.append(t)
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["rows"][1]["trade_date"]=t["datasets"]["252990"]["rows"][0]["trade_date"];cases.append(t)
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["rows"][0]["foreign_net_shares"]=10**15;cases.append(t)
        t=copy.deepcopy(baseline);t["confirmed_scoring_allowed"]=True;cases.append(t)
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["independent_exchange_session_verified"]=True;cases.append(t)
        t=copy.deepcopy(baseline);t["datasets"]["252990"]["row_count"]=300;cases.append(t)
        for item in cases:
            with self.subTest(change=item):
                with self.assertRaises(m.PrivateRollingFlowError):
                    m.combine_snapshots([item],asof="2026-10-16")
        with self.assertRaises(m.PrivateRollingFlowError):
            m.combine_snapshots([baseline,baseline],asof="2026-10-16")

    def test_local_only_atomic_write_no_public_raw_or_account_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"private-source"
            root.mkdir()
            (root/"investor_2026-10-16.json").write_text(
                json.dumps(source(business_rows(count=25))),encoding="utf8")
            out=Path(tmp)/"privately-owned"/"ias.json"
            captured=io.StringIO()
            with redirect_stdout(captured):
                rc=m.main(["--input-dir",str(root),"--asof","2026-10-16",
                           "--output-private",str(out)])
            self.assertEqual(rc,0)
            self.assertTrue(out.is_file())
            self.assertNotIn("252990",captured.getvalue())
            self.assertNotIn("foreign_net_shares",captured.getvalue().split('"periods":')[0])
            self.assertNotIn("20",str(json.loads(captured.getvalue())["ias_20_scored_count"]))
            self.assertEqual(json.loads(out.read_text())["records"]["252990"]
                             ["periods"]["20"]["foreign_net_shares"],200)
            # Fail safe: attempting to overwrite a private operator result
            # cannot lose original data or reveal source values.
            before=out.read_bytes()
            with redirect_stdout(captured):
                self.assertEqual(m.main(["--input-dir",str(root),
                                          "--asof","2026-10-16",
                                          "--output-private",str(out)]),1)
            self.assertEqual(out.read_bytes(),before)

    def test_git_checkout_cannot_be_used_as_raw_private_destination(self):
        with self.assertRaisesRegex(m.PrivateRollingFlowError,
                                    "raw_flow_repository_path_forbidden"):
            m._private_path(m.PRIVATE_ROOT/"docs"/"v3-ias-raw.json",must_exist=False)
        with self.assertRaisesRegex(m.PrivateRollingFlowError,
                                    "raw_flow_repository_path_forbidden"):
            m._private_path(m.PRIVATE_ROOT/"tests",is_directory=True)


if __name__=="__main__":
    unittest.main()
