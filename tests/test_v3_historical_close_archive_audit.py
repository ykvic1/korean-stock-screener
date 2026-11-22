import gzip
import json
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

import v3_historical_close_archive_audit as archive
from v3_regular_close_indicators import DAY_SCHEMA


class HistoricalCloseArchiveTests(TestCase):
    def test_price_identity_share_count_and_trading_status_are_independent(self):
        public = {
            "000001": ("KOSPI", "가", 20, 10, 10, 10, 10, 1),
            "000002": ("KOSPI", "나", 21, 10, 10, 10, 10, 0),
            "000003": ("KOSPI", "다", 22, 10, 10, 10, 10, 1),
            "000004": ("KOSPI", "라", None, 10, 10, 10, None, None),
            "000005": ("KOSPI", "마", 25, 10, 10, 10, 10, 1),
        }
        official = {
            "000001": ["KOSPI", "가", 10, 20, None, True],
            "000002": ["KOSPI", "나", 11, 20, None, True],
            "000003": ["KOSDAQ", "다", 10, 20, None, True],
            "000004": ["KOSPI", "라", 10, 20, None, None],
            "000006": ["KOSPI", "바", 10, 20, None, True],
        }
        counts = archive.compare(public, official, public.keys() | official.keys() | {"000007"})
        for key in archive.COMPARISON_STATES:
            self.assertEqual(counts[key], 1, key)
        self.assertEqual(counts["rows"], 7)
        for key in ("exact_listed_shares", "mismatch_listed_shares", "public_shares_unavailable",
                    "same_trade_status", "trade_status_mismatch", "trade_status_unavailable"):
            self.assertEqual(counts[key], 1, key)


    def test_successful_archive_must_match_all_saved_market_and_pilot_dimensions(self):
        public = {"000010": ("KOSPI", "가", 100, 10, 12, 9, 11, 10)}
        saved = {"000010": ["KOSPI", "가", 11, 100, None, True]}
        good = archive.compare(public, saved, {"000010"})
        report = {
            "market_sessions_expected": 1,
            "official_sessions_compared": 1,
            "official_sessions_missing": [],
            "new_market_api_calls": 0,
            "extreme_horizon_rows": 5,
            "distinct_adverse_extreme_dates": 1,
            "official_close_corroborated_extreme_dates": 1,
            "official_intraday_low_extremes_verified": False,
            "public_ohlc_bounds_are_not_independent_low_price_proof": True,
            "by_session": {"2025-11-13": {"market": good, "pilot": good}},
            "totals": {"market": good.copy(), "pilot": good.copy()},
        }
        self.assertTrue(archive.close_reconciliation_complete(report))
        self.assertEqual(good["traded_public_bars"], 1)
        self.assertEqual(good["official_close_inside_public_ohlc"], 1)

        for scope in ("market", "pilot"):
            for key in ("exact_close", "exact_listed_shares", "same_trade_status"):
                with self.subTest(scope=scope, missing_count=key):
                    damaged = deepcopy(report)
                    damaged["by_session"]["2025-11-13"][scope][key] = 0
                    self.assertFalse(archive.close_reconciliation_complete(damaged))

            for key in ("identity_mismatch", "close_mismatch", "mismatch_listed_shares",
                        "trade_status_mismatch", "public_row_missing"):
                with self.subTest(scope=scope, unexpected=key):
                    damaged = deepcopy(report)
                    damaged["totals"][scope][key] += 1
                    self.assertFalse(archive.close_reconciliation_complete(damaged))

        for field, value in (("official_sessions_compared", 0),
                             ("official_sessions_missing", ["2025-11-13"]),
                             ("new_market_api_calls", 1)):
            with self.subTest(incomplete=field):
                damaged = deepcopy(report)
                damaged[field] = value
                self.assertFalse(archive.close_reconciliation_complete(damaged))

        wrong = deepcopy(report)
        wrong["totals"]["market"]["rows"] += 1
        self.assertFalse(archive.close_reconciliation_complete(wrong))

        wrong = deepcopy(report)
        wrong["by_session"] = {}
        self.assertFalse(archive.close_reconciliation_complete(wrong))

        wrong = deepcopy(report)
        wrong["official_close_corroborated_extreme_dates"] = 0
        self.assertFalse(archive.close_reconciliation_complete(wrong))
        wrong = deepcopy(report)
        wrong["official_intraday_low_extremes_verified"] = True
        self.assertFalse(archive.close_reconciliation_complete(wrong))

    def test_inconsistent_public_high_low_is_not_proved_by_matching_closes(self):
        source = {"000010": ("KOSPI", "가", 100, 12, 10, 9, 11, 1)}
        official = {"000010": ["KOSPI", "가", 11, 100, None, True]}
        counts = archive.compare(source, official, {"000010"})
        self.assertEqual(counts["exact_close"], 1)
        self.assertEqual(counts["invalid_public_ohlc"], 1)
        self.assertEqual(counts["official_close_inside_public_ohlc"], 0)

        # A halt does not manufacture a high/low check.
        source["000010"] = ("KOSPI", "가", 100, None, None, None, 11, 0)
        official["000010"][-1] = False
        halted = archive.compare(source, official, {"000010"})
        self.assertEqual(halted["traded_public_bars"], 0)
        self.assertEqual(halted["invalid_public_ohlc"], 0)

    def test_partial_archive_keeps_explicit_missing_date_and_changes_pilot_symbol(self):
        original, successor = "000010", "000020"
        template = {code: (market, name, 100, 10, 10, 10, 10, 5) for code, market, name in (
            ("005930", "KOSPI", "삼성전자"), ("000660", "KOSPI", "SK하이닉스"),
            (original, "KOSPI", "원종목"), (successor, "KOSDAQ", "교부종목"),
        )}
        official = {code: [row[0], row[1], row[6] + (code == original), row[2], None, True]
                    for code, row in template.items()}
        dates = (archive.SIGNAL, archive.ENTRY, archive.END)
        signals = {date: template for date in dates}

        class Storage:
            def get_content(self, path):
                date = path.rsplit("/", 1)[1].removesuffix(".json.gz")
                if date == archive.ENTRY.isoformat():
                    return None, None
                payload = {"schema": DAY_SCHEMA, "date": date, "rows": official,
                           "source_sha256": {"KOSPI": "1" * 64, "KOSDAQ": "2" * 64}}
                return gzip.compress(json.dumps(payload).encode()), "sha"

        windows = [(original, archive.SIGNAL, archive.ENTRY),
                   (successor, archive.END, archive.END)]
        extremes = [{"code": original, "horizon_months": str(h),
                     "adverse_extreme_on": archive.ENTRY.isoformat()}
                    for h in (1, 2, 3, 6, 12)]
        with patch.object(archive, "pilot_windows", return_value=(1, windows)):
            result = archive.build(signals, Storage(), extreme_rows=extremes)
        self.assertEqual(result["official_sessions_missing"], [archive.ENTRY.isoformat()])
        self.assertEqual(result["market_sessions_expected"], 3)
        self.assertEqual(result["official_sessions_compared"], 2)
        self.assertEqual(result["totals"]["market"]["close_mismatch"], 2)
        self.assertEqual(result["totals"]["pilot"]["close_mismatch"], 1)
        self.assertEqual(result["totals"]["pilot"]["exact_close"], 1)
        self.assertFalse(result["performance_validated"])
        self.assertEqual(result["extreme_horizon_rows"], 5)
        self.assertEqual(result["distinct_adverse_extreme_dates"], 1)
        self.assertEqual(result["official_close_corroborated_extreme_dates"], 0)
        self.assertFalse(result["official_intraday_low_extremes_verified"])
        self.assertFalse(archive.close_reconciliation_complete(result))
