"""No-network partial 60-159-bar research and all-current-listed market guards."""
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from v22_short_history_observations import (
    build_short_history,write_short_history,SCHEMA,
)


DAY="2026-10-19"


def frame(bars,*,stale=False,invalid=None):
    idx=pd.date_range(end="2026-10-17" if stale else DAY,
                      periods=bars,freq="B")
    close=[100+i/5 for i in range(bars)]
    data=pd.DataFrame({
        "Open":[x-0.5 for x in close],
        "High":[x+1 for x in close],
        "Low":[x-1 for x in close],
        "Close":close,
        "Volume":[1000+10*i for i in range(bars)],
    },index=idx)
    if invalid=="zero_volume":data.iloc[-1,data.columns.get_loc("Volume")]=0
    if invalid=="bad_low":data.iloc[-1,data.columns.get_loc("Low")]=100000
    if invalid=="missing_volume":data=data.drop(columns=["Volume"])
    return data


def sample(count=23):
    return {
        "version":"2.0","status":"success","date":DAY,
        "stocks":[{"code":"005930","market":"KOSPI","price":1000,"price_date":DAY}],
        "history_exclusions":[[f"{430000+i:06d}","insufficient_valid_price_history"]
                              for i in range(count)],
    }


class PartialShortHistoryStockTests(unittest.TestCase):
    def test_all_23_issuer_sample_13_exploratory_observed_10_under60(self):
        record=sample()
        codes=[x[0] for x in record["history_exclusions"]]
        lengths=[130]*5+[80]*8+[35]*6+[15]*4
        frames={code:frame(n) for code,n in zip(codes,lengths)}
        result=build_short_history(record,lambda code,start,end:frames[code],
                                   cfg={"historical_days":1100,"request_sleep_seconds":0},
                                   sleep=lambda _:None)
        self.assertEqual(result["schema"],SCHEMA)
        self.assertEqual(result["asof"],DAY)
        self.assertEqual(result["listed_insufficient_history_count"],23)
        self.assertEqual(result["partial_observed_count"],13)
        self.assertEqual(result["still_unavailable_count"],10)
        self.assertEqual(len(result["rows"]),23)
        self.assertFalse(result["full_v22_score_allowed"])
        self.assertFalse(result["confirmed_buy_signal"])
        self.assertFalse(result["raw_ohlcv_published"])
        self.assertTrue(result["daily_ma120_is_not_weekly_ma120"])
        observed=[x for x in result["rows"] if x["status"]=="partial_technical_observed"]
        self.assertEqual(len(observed),13)
        self.assertEqual(sum(x["ma120_daily"] is not None for x in observed),5)
        self.assertTrue(all(x["close_reference"]>0 and x["ma20"]>0 and x["ma60"]>0 for x in observed))
        self.assertTrue(all("stop_line" not in x and "score" not in x and "v3_buy_signal" not in x
                            for x in result["rows"]))
        unavailable=[x for x in result["rows"] if x["status"]!="partial_technical_observed"]
        self.assertEqual(len(unavailable),10)
        self.assertTrue(all("close_reference" not in x for x in unavailable))
        self.assertFalse(any(x["bars"]>=160 for x in result["rows"]))

    def test_source_errors_or_bad_prices_cannot_gain_partial_status(self):
        s=sample(4)
        codes=[x[0] for x in s["history_exclusions"]]
        frames={codes[0]:frame(120,stale=True),codes[1]:frame(90,invalid="zero_volume"),
                codes[2]:frame(90,invalid="bad_low"),codes[3]:frame(90,invalid="missing_volume")}
        result=build_short_history(s,lambda c,start,end:frames[c],
                                   cfg={"historical_days":1100,"request_sleep_seconds":0},
                                   sleep=lambda _:None)
        self.assertEqual(result["partial_observed_count"],0)
        self.assertEqual(result["still_unavailable_count"],4)
        self.assertTrue(all(x["status"]=="not_observable_partial" for x in result["rows"]))
        self.assertNotIn("Close",json.dumps(result))
        self.assertNotIn("Volume",json.dumps(result))

    def test_no_contamination_of_full_price_or_duplicate_case_or_changed_day(self):
        snap=sample(1)
        snap["history_exclusions"][0][0]="005930"
        with self.assertRaisesRegex(ValueError,"already_full_scored"):
            build_short_history(snap,lambda *args:frame(90))
        snap=sample(1)
        with self.assertRaisesRegex(ValueError,"source_day_mismatch"):
            build_short_history(snap,lambda *args:frame(90),asof="2026-10-16")
        snap=sample(2)
        snap["history_exclusions"][1][0]=snap["history_exclusions"][0][0]
        with self.assertRaisesRegex(ValueError,"duplicate"):
            build_short_history(snap,lambda *args:frame(90))
        snap=sample(81)
        with self.assertRaisesRegex(ValueError,"population_invalid"):
            build_short_history(snap,lambda *args:frame(90))

    def test_file_publishes_only_derived_observations_no_vendor_bar_copy(self):
        snap=sample(2)
        codes=[x[0] for x in snap["history_exclusions"]]
        frames={codes[0]:frame(100),codes[1]:frame(12)}
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/"docs"/"v22-partial-history.json"
            out=write_short_history(snap,dest,reader=lambda code,start,end:frames[code],
                                    cfg={"historical_days":1100,"request_sleep_seconds":0},
                                    sleep=lambda _:None)
            self.assertEqual(out["partial_observed_count"],1)
            self.assertEqual(out["still_unavailable_count"],1)
            self.assertNotIn("rows",out)
            on_disk=json.loads(dest.read_text(encoding="utf8"))
            self.assertEqual(on_disk["asof"],DAY)
            self.assertFalse(on_disk["raw_ohlcv_published"])
            source=dest.read_text(encoding="utf8")
            for prohibited in ("AUTH_KEY","Raw OHLCV","marketOrder","target_first",
                               "confirmed_buy",'"stop_line"','"price_history_full_score"'):
                self.assertNotIn(prohibited,source)

    def test_publish_wiring_keeps_legacy_price_score_source_unchanged(self):
        root=Path(__file__).resolve().parents[1]
        batch=(root/"full_market_batch.py").read_text(encoding="utf8")
        workflow=(root/".github/workflows/full-market-batches.yml").read_text(encoding="utf8")
        self.assertIn('write_short_history(data, screener.SITE / "v22-partial-history.json"',batch)
        self.assertIn("docs/v22-partial-history.json",workflow)
        self.assertIn("full_market_batch.py merge",workflow)
        self.assertIn("github.event_name == 'schedule'",workflow)


if __name__=="__main__":
    unittest.main()
