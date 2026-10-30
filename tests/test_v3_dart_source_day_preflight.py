"""Synthetic, zero-network source-date gate and workflow safety tests."""
import copy
from datetime import date,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_v3_dart_rolling_book_reference import fixture,DAY
from v3_dart_source_day_preflight import (
    check, main, SourceDayGateError,
)
from v3_dart_bulk_history import SCHEMA as BULK_SCHEMA


ROOT=Path(__file__).resolve().parents[1]


def sources():
    market,catalog=fixture()
    annual={"schema":"v3-dart-current-listed-annual-book-reference-0.1",
            "asof":DAY,"market_listed_total":catalog["listed_total"],
            "historical_point_in_time_verified":False,
            "official_exchange_pbr_verified":False,
            "observations":[]}
    major={"schema":BULK_SCHEMA,"asof":DAY,
           "listed_total":catalog["listed_total"],
           "fiscal_years":[2022,2023,2024,2025],
           "historical_PIT_source_certified":False,
           "raw_source_rows_published":False,"records":{}}
    return market,catalog,annual,major


def moved(market,catalog,new_day):
    market=copy.deepcopy(market)
    catalog=copy.deepcopy(catalog)
    market["date"]=catalog["date"]=new_day
    for stock in market["stocks"]:
        stock["price_date"]=new_day
    return market,catalog


class DARTSourceDayPreflightTests(unittest.TestCase):
    def test_both_sources_ready_on_exact_market_close(self):
        market,catalog,annual,major=sources()
        for kind,source in (("annual",annual),("major",major)):
            got=check(market,catalog,source,kind=kind)
            self.assertTrue(got["ready"])
            self.assertEqual(got["day_lag"],0)
            self.assertEqual(got["extra_official_calls"],0)
            self.assertFalse(got["full_v3_100pt_certified"])

    def test_bounded_one_day_lag_deferred_without_fabricated_values(self):
        market,catalog,annual,major=sources()
        latest=(date.fromisoformat(DAY)+timedelta(days=1)).isoformat()
        market,catalog=moved(market,catalog,latest)
        for kind,source in (("annual",annual),("major",major)):
            got=check(market,catalog,source,kind=kind)
            self.assertFalse(got["ready"])
            self.assertEqual(got["market_asof"],latest)
            self.assertEqual(got["source_asof"],DAY)
            self.assertEqual(got["status"],"deferred_waiting_for_dart_same_market_day")
        self.assertEqual(annual["asof"],DAY)
        self.assertEqual(major["asof"],DAY)

    def test_new_listings_defer_previous_census_but_reject_relabeling(self):
        market,catalog,annual,major=sources()
        latest=(date.fromisoformat(DAY)+timedelta(days=1)).isoformat()
        market,catalog=moved(market,catalog,latest)
        # Before today's close there were four issuers in the saved annual
        # and bulk sources. Today's independently valid roster has five.
        annual["market_listed_total"]=4
        major["listed_total"]=4
        for kind,source in (("annual",annual),("major",major)):
            got=check(market,catalog,source,kind=kind)
            self.assertFalse(got["ready"])
            self.assertEqual(got["source_listed_total"],4)
            self.assertEqual(got["current_listed_total"],5)
            self.assertTrue(got["census_changed_while_waiting"])
            self.assertEqual(got["extra_official_calls"],0)
            self.assertFalse(got["full_v3_100pt_certified"])
            spoof=copy.deepcopy(source)
            spoof["asof"]=latest
            with self.assertRaisesRegex(
                    SourceDayGateError,kind+"_reference_not_source_verified"):
                check(market,catalog,spoof,kind=kind)

    def test_invalid_prior_census_metadata_must_not_be_called_a_wait(self):
        market,catalog,annual,major=sources()
        latest=(date.fromisoformat(DAY)+timedelta(days=1)).isoformat()
        market,catalog=moved(market,catalog,latest)
        for kind,source,field in (
                ("annual",annual,"market_listed_total"),
                ("major",major,"listed_total")):
            for bad_count in (True,0,-1,6001,"5"):
                with self.subTest(kind=kind,bad_count=bad_count):
                    wrong=copy.deepcopy(source)
                    wrong[field]=bad_count
                    with self.assertRaises(SourceDayGateError):
                        check(market,catalog,wrong,kind=kind)
            wrong=copy.deepcopy(source)
            wrong["schema"]="forged-source-schema"
            with self.assertRaises(SourceDayGateError):
                check(market,catalog,wrong,kind=kind)
        wrong=copy.deepcopy(major)
        wrong["fiscal_years"]=[2021,2022,2023,2024]
        with self.assertRaisesRegex(SourceDayGateError,
                                    "major_reference_not_source_verified"):
            check(market,catalog,wrong,kind="major")

    def test_future_expired_or_tampered_reference_does_not_get_deferred(self):
        market,catalog,annual,major=sources()
        for kind,source in (("annual",annual),("major",major)):
            ahead=copy.deepcopy(source)
            ahead["asof"]=(date.fromisoformat(DAY)+timedelta(days=1)).isoformat()
            with self.assertRaises(SourceDayGateError):
                check(market,catalog,ahead,kind=kind)
            old=copy.deepcopy(source)
            old["asof"]=(date.fromisoformat(DAY)-timedelta(days=11)).isoformat()
            with self.assertRaises(SourceDayGateError):
                check(market,catalog,old,kind=kind)
            invalid=copy.deepcopy(source)
            if kind=="annual":
                invalid["official_exchange_pbr_verified"]=True
            else:
                invalid["raw_source_rows_published"]=True
            with self.assertRaises(SourceDayGateError):
                check(market,catalog,invalid,kind=kind)
        wrong=copy.deepcopy(major)
        wrong["fiscal_years"]=[2021,2022,2023,2024]
        with self.assertRaises(SourceDayGateError):
            check(market,catalog,wrong,kind="major")

    def test_no_network_cli_writes_only_boolean_output_without_rewriting_data(self):
        market,catalog,annual,major=sources()
        later=(date.fromisoformat(DAY)+timedelta(days=1)).isoformat()
        market,catalog=moved(market,catalog,later)
        annual["market_listed_total"]=4
        major["listed_total"]=4
        with tempfile.TemporaryDirectory() as place:
            root=Path(place)
            files={"data.json":market,"v22-market-catalog.json":catalog,
                   "v3-dart-book-reference.json":annual,
                   "v3-dart-bulk-history.json":major}
            for name,doc in files.items():
                (root/name).write_text(json.dumps(doc),encoding="utf8")
            output=root/"output.txt"
            import contextlib
            import io
            with patch.dict("os.environ",{"GITHUB_OUTPUT":str(output)}):
                with contextlib.redirect_stdout(io.StringIO()) as capture:
                    main(["--docs",str(root),"--kind","major"])
            res=json.loads(capture.getvalue())
            self.assertEqual(res["status"],"deferred_waiting_for_dart_same_market_day")
            self.assertTrue(res["census_changed_while_waiting"])
            self.assertEqual(res["source_listed_total"],4)
            self.assertIn("ready=false",output.read_text(encoding="utf8"))
            self.assertEqual(json.loads((root/"v3-dart-bulk-history.json").read_text()),
                             major)

    def test_cashflow_and_bulk_workflows_do_not_run_provider_when_lagged(self):
        for workflow in ("v3-dart-cashflow.yml","v3-dart-bulk-four-year.yml"):
            src=(ROOT/".github"/"workflows"/workflow).read_text(encoding="utf8")
            self.assertIn("id: date_gate",src)
            self.assertIn("v3_dart_source_day_preflight.py --kind ",src)
            self.assertGreaterEqual(src.count(
                "steps.date_gate.outputs.ready == 'true'"),2)
            self.assertIn("if: always()",src)
            self.assertIn("DAY_READY:",src)
            self.assertIn("cancel-in-progress: false",src)
            self.assertNotIn("git push --force",src)


if __name__=="__main__":
    unittest.main()
