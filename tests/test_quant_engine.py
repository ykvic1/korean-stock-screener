import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from quant_engine import (label_future,wilson,build_features,StudyCase,
                          analyze_nearest,run,read_ohlcv,
                          classify_source_exclusion,source_quality_audit,
                          source_cohort_digest,load_collector_failures)

class QuantTests(unittest.TestCase):
    def mk(self, rows):
        d=pd.DataFrame(rows,columns=['Date','Open','High','Low','Close','Volume'])
        d['Date']=pd.to_datetime(d.Date)
        return d.set_index('Date')
    def test_next_day_open_not_signal_close(self):
        df=self.mk([['2026-01-02',100,100,100,100,1000],
                    ['2026-01-05',105,106,102,104,1000],
                    ['2026-01-06',107,108,106,107,1000]])
        x=label_future(df,0,2,.30,.20,.40)
        self.assertAlmostEqual(x['return_pct'],(107/105-1)*100-.40,places=5)
    def test_same_bar_stop_first_and_gap(self):
        df=self.mk([['2026-01-02',100,100,100,100,1000],
                    ['2026-01-05',100,115,90,100,1000],
                    ['2026-01-06',100,101,99,100,1000]])
        x=label_future(df,0,1,.1,.05,.40)
        self.assertTrue(x['hit_stop_first'])
        self.assertFalse(x['win_target_first'])
        self.assertAlmostEqual(x['return_pct'],-5.4)
        df.iloc[1,df.columns.get_loc('Low')]=84
        df.iloc[1,df.columns.get_loc('Open')]=90
        df.iloc[1,df.columns.get_loc('High')]=110
        x=label_future(df,0,1,.1,.05,.40)
        self.assertEqual(x['gap_count'],0) # entry is next open, stop is anchored to 90
    def test_wilson(self):
        l,h=wilson(50,100)
        self.assertLess(l,50);self.assertGreater(h,50)
    def test_features_ignore_future(self):
        dates=pd.bdate_range('2020-01-01',periods=760)
        c=100+np.arange(len(dates))*.02
        df=pd.DataFrame(dict(Open=c,High=c*1.005,Low=c*.995,Close=c,Volume=np.full(len(c),1000)),index=dates)
        orig=build_features(df)[-50].copy()
        df.iloc[-1,df.columns.get_loc('Close')]*=1.20
        df.iloc[-1,df.columns.get_loc('High')]*=1.20
        new=build_features(df)[-50].copy()
        np.testing.assert_allclose(orig,new)
    def test_insufficient_samples_never_impute(self):
        study=analyze_nearest('000001',np.zeros(7), 'KOSPI', [],'스윙 1개월',.4,.1,.07)
        self.assertEqual(study['status'],'insufficient')
        self.assertNotIn('up_pct',study)
    def test_nearest_empirical(self):
        cases=[]
        for i in range(60):
            cases.append(StudyCase(f'{i%12+1:06d}','KOSPI',f'202{i%6}-01-03',str(i%12),
                    [0.0]*7,2 if i%2 else -4,2 if i%2 else -4,10,6,i%3==0,i%4==0,0))
        s=analyze_nearest('123456',np.zeros(7),'KOSPI',cases,'스윙 1개월',.4,.1,.07)
        self.assertEqual(s['status'],'exploratory')
        self.assertEqual(s['sample_n'],60)
        self.assertEqual(s['up_pct'],50.0)
        self.assertTrue(0<=s['stop_first_pct']<=100)
    def test_no_history_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'data.json').write_text(json.dumps({'status':'success','date':'2026-10-14','stocks':[{'code':'005930','market':'KOSPI'}]}),encoding='utf-8')
            x=run(p/'data.json',p/'missing',p/'quant.json')
            self.assertEqual(x['status'],'insufficient')
            self.assertEqual(x['available_symbols'],0)
            self.assertTrue((p/'quant.json').exists())
    def test_every_missing_price_history_is_accounted_for(self):
        missing=[{"code":f"{i:06d}","error":"not found",
                  "reason_code":"history_file_missing"} for i in range(51)]
        audit=source_quality_audit(120,69,missing)
        self.assertEqual(audit["requested_symbols"],120)
        self.assertEqual(audit["valid_ohlcv_symbols"],69)
        self.assertEqual(audit["excluded_symbols"],51)
        self.assertEqual(audit["excluded_reason_counts"]["history_file_missing"],51)
        self.assertTrue(audit["all_exclusions_listed"])
        self.assertFalse(audit["official_adjusted_prices_certified"])

    def test_ohlcv_rejection_categories_are_not_fabricated_repairs(self):
        self.assertEqual(classify_source_exclusion(FileNotFoundError("a.csv")),
                         "history_file_missing")
        self.assertEqual(classify_source_exclusion(ValueError("종가 범위 오류")),
                         "ohlc_range_invalid")
        self.assertEqual(classify_source_exclusion(
            ValueError("음수 또는 0인 가격 / 음수 거래량")),
            "nonpositive_price_or_negative_volume")
        self.assertEqual(classify_source_exclusion(
            ValueError("마지막 OHLCV 날짜가 신호일과 다름")),
            "source_date_mismatch")
        self.assertEqual(classify_source_exclusion(RuntimeError("unknown")),
                         "other_unverified_source_error")

    def test_collector_errors_require_exact_same_day_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            status={"asof":"2026-11-02","requested":1,
                    "cohort_sha256":source_cohort_digest("2026-11-02",["005930"]),
                    "fetched":0,"reused":0,"failed_n":1,
                    "failed":[["005930","종가 범위 오류"]]}
            (folder/"cache_status.json").write_text(
                json.dumps(status),encoding="utf-8")
            self.assertEqual(load_collector_failures(
                folder,"2026-11-02",["005930"]),{"005930":"종가 범위 오류"})
            self.assertEqual(load_collector_failures(
                folder,"2026-11-03",["005930"]),{})
            self.assertEqual(load_collector_failures(
                folder,"2026-11-02",["000660"]),{})
            changed={**status,"failed":[["000660","종가 범위 오류"]]}
            (folder/"cache_status.json").write_text(
                json.dumps(changed),encoding="utf-8")
            self.assertEqual(load_collector_failures(
                folder,"2026-11-02",["005930"]),{})
            changed={**status,"failed_n":2}
            (folder/"cache_status.json").write_text(
                json.dumps(changed),encoding="utf-8")
            self.assertEqual(load_collector_failures(
                folder,"2026-11-02",["005930"]),{})

    def test_rejected_provider_ohlc_reason_survives_missing_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            signal=folder/"data.json"
            signal.write_text(json.dumps({"status":"success",
                "date":"2026-11-02","stocks":[{"code":"005930","market":"KOSPI"}]}),
                encoding="utf-8")
            history=folder/"history"
            history.mkdir()
            status={"asof":"2026-11-02","requested":1,
                    "cohort_sha256":source_cohort_digest("2026-11-02",["005930"]),
                    "fetched":0,"reused":0,"failed_n":1,
                    "failed":[["005930","종가 범위 오류"]]}
            (history/"cache_status.json").write_text(
                json.dumps(status),encoding="utf-8")
            out=run(signal,history,folder/"quant.json")
            self.assertEqual(out["missing"][0]["reason_code"],
                             "ohlc_range_invalid")
            self.assertEqual(out["missing"][0]["error_provenance"],
                             "matched_same_day_collector")
            self.assertEqual(out["available_symbols"],0)
            self.assertEqual(out["source_quality_audit"]["excluded_symbols"],1)
            # A mismatched source day cannot overwrite the observed reason.
            status["cohort_sha256"]=source_cohort_digest(
                "2026-11-01",["005930"])
            (history/"cache_status.json").write_text(
                json.dumps(status),encoding="utf-8")
            stale=run(signal,history,folder/"quant2.json")
            self.assertEqual(stale["missing"][0]["reason_code"],
                             "history_file_missing")

    def test_ohlc_price_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'005930.csv'
            dates=pd.bdate_range('2022-01-01',periods=670)
            df=pd.DataFrame({'Date':dates.strftime('%Y-%m-%d'), 'Open':100,'High':101,'Low':99,'Close':100,'Volume':1000})
            df.to_csv(p,index=False)
            self.assertEqual(len(read_ohlcv(p,dates[-1].date().isoformat())),670)
            df.loc[3,'Low']=102
            df.to_csv(p,index=False)
            with self.assertRaises(ValueError):read_ohlcv(p,dates[-1].date().isoformat())

if __name__ == '__main__': unittest.main()
