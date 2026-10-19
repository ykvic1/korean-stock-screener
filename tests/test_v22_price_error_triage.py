"""v2.2 daily full-market price failure triage, strict and provider-safe."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

import full_market_batch
import screener
from v22_price_error_triage import public_reason, REASONS
from v22_price_reason_recheck_once import sample_errors

DAY="2026-10-19"


class PriceTriageTests(unittest.TestCase):
    def test_strict_known_cause_classification_avoids_provider_error_strings(self):
        examples=[
            (ValueError("가격 이력 없음"),"price_empty_history"),
            (ValueError("필수 OHLCV 누락"),"price_missing_ohlcv_columns"),
            (ValueError("OHLCV 결측 데이터"),"price_null_ohlcv"),
            (ValueError("현재가/거래량 오류"),"price_zero_last_volume_or_close"),
            (ValueError("가격 이력 최신성 오류"),"price_stale_history"),
            (ValueError("could not convert string to float: 'xxxx'"),"price_numeric_conversion"),
            (ValueError("https://provider.invalid?AUTH_KEY=secret"),"price_other_value_error"),
            (TimeoutError("SECRET_API_KEY"),"price_network_timeout"),
            (ConnectionError("SECRET_API_KEY"),"price_network_connection"),
            (RuntimeError("SECRET_API_KEY"),"price_unexpected_exception"),
        ]
        for error,expected in examples:
            with self.subTest(error=type(error).__name__,reason=expected):
                self.assertEqual(public_reason(error),expected)
                self.assertIn(expected,REASONS)
                self.assertNotIn("secret",public_reason(error).lower())
        self.assertEqual(public_reason(ValueError("cannot convert string to float")), "price_other_value_error")

    def test_real_scan_keeps_exception_secret_absent_and_price_count_exact(self):
        listing={}
        for i in range(101):
            code=f"{100000+i:06d}"
            listing[code]={"code":code,"market":"KOSPI","name":f"Synthetic {i}"}
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            full_market_batch.plan(asof=DAY,out=p,listing=listing)
            manifest=json.loads((p/"manifest.json").read_text(encoding="utf-8"))
            outcomes=[
                ValueError("현재가/거래량 오류"),
                ValueError("가격 이력 최신성 오류"),
                ValueError("OHLCV 결측 데이터"),
                ValueError("API_KEY=PRIVATE_TOKEN"),
                screener.InsufficientHistory("incomplete")
            ]
            def analyzer(code,cfg,day):
                i=int(code)
                if i-100000<len(outcomes):raise outcomes[i-100000]
                return {"price_date":DAY,"price_latest":3000.0}
            for part in range(manifest["batch_count"]):
                full_market_batch.scan(manifest=manifest,part=part,out=p,
                                       analyzer=analyzer,sleep=lambda _:None)
            combined=full_market_batch.load_receipts(manifest,p)
            self.assertEqual(combined["eligible"],101)
            self.assertEqual(combined["examined"],101)
            self.assertEqual(len(combined["rows"]),96)
            self.assertEqual(len(combined["errors"]),4)
            self.assertEqual(len(combined["excluded"]),1)
            errors=Counter(e[2] for e in combined["errors"])
            self.assertEqual(errors,{
                "price_zero_last_volume_or_close":1,
                "price_stale_history":1,
                "price_null_ohlcv":1,
                "price_other_value_error":1
            })
            serial="".join((p/f"part-{i:03d}.json").read_text(encoding="utf8")
                           for i in range(manifest["batch_count"]))
            self.assertNotIn("PRIVATE_TOKEN",serial)
            self.assertNotIn("API_KEY",serial)
            self.assertNotIn("가격 이력 최신성 오류",serial)
            self.assertTrue(all(len(row)==3 for row in combined["errors"]))

    def test_bounded_stratified_current_listed_recheck_not_full_market_loop(self):
        rows=[]
        for i in range(106):
            rows.append({"code":f"{500000+i:06d}",
                         "name":f"Synthetic {i}",
                         "market":"KOSDAQ" if i%2 else "KOSPI",
                         "price_history_status":"price_source_or_validation_error"})
        catalog={
            "schema":"v22-current-listed-catalog-0.1",
            "date":DAY,"listed_total":len(rows),"rows":rows}
        chosen,n=sample_errors(catalog,DAY)
        self.assertEqual(n,106)
        self.assertEqual(len(chosen),24)
        self.assertEqual(len(set(chosen)),24)
        self.assertTrue(all(code in {r["code"] for r in rows} for code in chosen))
        with self.assertRaisesRegex(ValueError,"outdated"):
            sample_errors({**catalog,"date":"2026-10-16"},DAY)
        with self.assertRaisesRegex(ValueError,"unaccounted"):
            sample_errors({**catalog,"listed_total":105},DAY)
        with self.assertRaisesRegex(ValueError,"invalid"):
            sample_errors({**catalog,"rows":rows[:2],"listed_total":2},DAY)


if __name__=="__main__":
    unittest.main()
