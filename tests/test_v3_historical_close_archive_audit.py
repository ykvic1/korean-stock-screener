import gzip
import json
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
        with patch.object(archive, "pilot_windows", return_value=(1, windows)):
            result = archive.build(signals, Storage())
        self.assertEqual(result["official_sessions_missing"], [archive.ENTRY.isoformat()])
        self.assertEqual(result["market_sessions_expected"], 3)
        self.assertEqual(result["official_sessions_compared"], 2)
        self.assertEqual(result["totals"]["market"]["close_mismatch"], 2)
        self.assertEqual(result["totals"]["pilot"]["close_mismatch"], 1)
        self.assertEqual(result["totals"]["pilot"]["exact_close"], 1)
        self.assertFalse(result["performance_validated"])
