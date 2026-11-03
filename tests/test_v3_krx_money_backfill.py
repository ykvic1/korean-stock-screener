import base64
from datetime import date, timedelta
from hashlib import sha256
import json
import unittest

from v3_krx_money_5day import (
    derive, validate_money_blob, DerivedSourceError, store_money_five_days,
)
from v3_krx_money_backfill import collect, _manifest, SCHEMA as CHECKPOINT_SCHEMA


def sample_days(n=10):
    days = []
    current = date(2026, 10, 5)
    while len(days) < n:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


class FakeClient:
    def __init__(self, missing_amount=False, multiplier=1):
        self.requests = []
        self.missing_amount = missing_amount
        self.multiplier = multiplier

    def daily_market(self, market, day):
        self.requests.append((market, day))
        index = sample_days().index(day)
        row = {
            "BAS_DD": day.replace("-", ""),
            "ISU_CD": "000001",
            "TDD_OPNPRC": str(100 + index),
            "TDD_HGPRC": str(101 + index),
            "TDD_LWPRC": str(99 + index),
            "TDD_CLSPRC": str(100 + index),
            "ACC_TRDVOL": "1000",
        }
        if not self.missing_amount:
            row["ACC_TRDVAL"] = str((index + 1) * 10_000_000 * self.multiplier)
        payload = json.dumps({"OutBlock_1": [row]}, separators=(",", ":")).encode()
        return [row], payload


class FakePrivateStore:
    repository = "ykvic1/korean-stock-private-data"

    def __init__(self):
        self.files = {}

    def verify_private(self):
        return True

    def get_content(self, path):
        payload = self.files.get(path)
        return payload, sha256(payload).hexdigest() if payload else None

    def put_new(self, path, payload, message):
        if path in self.files:
            raise ValueError("existing path overwritten")
        self.files[path] = payload

    def transport(self, method, path, body):
        assert method == "PUT" and path.startswith("contents/")
        assert body.get("sha") == sha256(self.files[path[9:]]).hexdigest()
        self.files[path[9:]] = base64.b64decode(body["content"])
        return {}


class ActualMoneyDerivationTests(unittest.TestCase):
    def test_trade_value_is_exact_reported_field_not_close_times_volume(self):
        days = sample_days()[:5]
        result, blob, raw_len = derive(FakeClient(), days)
        observed = result["derived_issuer_money_factors"]["KOSPI:000001"]
        self.assertEqual(observed["total_actual_amount_100k_krw"], 1500)
        self.assertEqual(observed["money_pressure_intrabucket_bp"], 10000)
        self.assertEqual(observed["positive_amount_days"], 5)
        self.assertFalse(observed["regular_session_only_verified"])
        self.assertFalse(observed["score_authorized"])
        self.assertGreater(raw_len, 0)
        self.assertFalse(result["daily_amount_or_price_bars_retained"])
        self.assertNotIn("10000000", blob.decode("latin1"))
        self.assertEqual(validate_money_blob(blob, days[0], days[-1]), result)
        scaled, _, _ = derive(FakeClient(multiplier=10), days)
        self.assertEqual(
            scaled["derived_issuer_money_factors"]["KOSPI:000001"]["total_actual_amount_100k_krw"],
            15000,
        )

    def test_missing_reported_money_never_substitutes_share_proxy(self):
        days = sample_days()[:5]
        result, blob, _ = derive(FakeClient(missing_amount=True), days)
        self.assertEqual(result["derived_issuer_money_factors"], {})
        self.assertEqual(result["complete_five_session_issuer_market_pairs"], 0)
        self.assertFalse(validate_money_blob(blob, days[0], days[-1])["investment_scoring_allowed"])

    def test_private_backfill_checkpoint_restarts_without_refetch(self):
        days = sample_days()
        store = FakePrivateStore()
        client = FakeClient()
        digest = "b" * 64
        first = collect(storage=store, client=client, dates=days,
                        checksum=digest, windows=1)
        self.assertEqual(first["completed_five_session_cohorts"], 1)
        self.assertEqual(first["current_run_api_calls"], 10)
        self.assertEqual(first["historical_cohorts_remaining"], 1)
        self.assertEqual(len(client.requests), 10)
        again = collect(storage=store, client=client, dates=days,
                        checksum=digest, windows=1)
        self.assertEqual(again["completed_five_session_cohorts"], 2)
        self.assertEqual(len(client.requests), 20)
        final = collect(storage=store, client=client, dates=days,
                        checksum=digest, windows=1)
        self.assertEqual(final["historical_cohorts_remaining"], 0)
        self.assertEqual(final["current_run_api_calls"], 0)
        self.assertEqual(len(client.requests), 20)
        saved = json.loads(store.files["derived/money-historical-checkpoint.json"])
        self.assertEqual(saved["schema"], CHECKPOINT_SCHEMA)
        self.assertEqual(saved["next_window_index"], 2)
        self.assertEqual(saved["raw_source_file_count"], 0)

    def test_corrupt_or_raw_column_blob_is_rejected(self):
        days = sample_days()[:5]
        result, blob, _ = derive(FakeClient(), days)
        del blob
        import gzip
        result["derived_issuer_money_factors"]["KOSPI:000001"]["ACC_TRDVAL"] = "secret"
        tampered = gzip.compress(json.dumps(result).encode(), mtime=0)
        with self.assertRaisesRegex(DerivedSourceError, "private_money_derived_blob_invalid"):
            validate_money_blob(tampered, days[0], days[-1])


if __name__ == "__main__":
    unittest.main()
