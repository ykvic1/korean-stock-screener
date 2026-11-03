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
                          source_cohort_digest,load_collector_failures,
                          load_collector_tail_diagnostics)

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
        self.assertEqual(classify_source_exclusion(
            ValueError("650봉 미만으로 120주 지표/과거 사례 부족")),
            "history_too_short")
        self.assertEqual(classify_source_exclusion(
            ValueError("가격 기준일 불일치: 2026-11-01 vs 2026-11-02")),
            "source_date_mismatch")
        self.assertEqual(classify_source_exclusion(
            ValueError("OHLCV 컬럼 누락")),
            "incomplete_or_invalid_fields")
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
                "date":"2026-11-02","stocks":[{"code":"005930","market":"KOSPI",
                "price":100,"price_date":"2026-11-02"}]}),
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

    def test_tail_diagnostic_requires_matching_failed_cohort_and_stays_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            signal=folder/"data.json"
            signal.write_text(json.dumps({"status":"success",
                "date":"2026-11-02","stocks":[{"code":"005930","market":"KOSPI",
                "price":100,"price_date":"2026-11-02"}]}),
                encoding="utf-8")
            history=folder/"history"
            history.mkdir()
            diag={"suspect_bar_count":2,"last_suspect_date":"2023-11-01",
                  "trailing_valid_sessions":651,"trailing_650_session_candidate":True,
                  "research_only":True,"official_adjustment_certified":False}
            status={"asof":"2026-11-02","requested":1,
                    "cohort_sha256":source_cohort_digest("2026-11-02",["005930"]),
                    "fetched":0,"reused":0,"failed_n":1,
                    "failed":[["005930","종가 범위 오류"]],
                    "failed_bar_diagnostics":[["005930",diag]]}
            file=history/"cache_status.json"
            file.write_text(json.dumps(status),encoding="utf-8")
            matched=load_collector_failures(history,"2026-11-02",["005930"])
            self.assertEqual(load_collector_tail_diagnostics(
                history,"2026-11-02",["005930"],matched)["005930"],diag)
            output=run(signal,history,folder/"quant.json")
            self.assertEqual(output["available_symbols"],0)
            self.assertEqual(output["missing"][0]["tail_diagnostic"],diag)
            self.assertEqual(output["source_quality_audit"]
                             ["unverified_tail_650_candidates"],1)
            self.assertFalse((history/"005930.csv").exists())
            self.assertFalse(output["source_quality_audit"]
                             ["official_adjusted_prices_certified"])
            stale={**status,"asof":"2026-11-01"}
            file.write_text(json.dumps(stale),encoding="utf-8")
            missing=run(signal,history,folder/"quant_stale.json")
            self.assertNotIn("tail_diagnostic",missing["missing"][0])
            self.assertEqual(missing["source_quality_audit"]
                             ["unverified_tail_650_candidates"],0)

    def test_tail_diagnostic_rejects_mismatched_or_fabricated_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            status={"asof":"2026-11-02","requested":1,
                    "cohort_sha256":source_cohort_digest("2026-11-02",["005930"]),
                    "fetched":0,"reused":0,"failed_n":1,
                    "failed":[["005930","종가 범위 오류"]],
                    "failed_bar_diagnostics":[["005930",{
                        "suspect_bar_count":1,"last_suspect_date":"2026-01-01",
                        "trailing_valid_sessions":3,
                        "trailing_650_session_candidate":True,
                        "research_only":True,
                        "official_adjustment_certified":False}]]}
            file=folder/"cache_status.json"
            errors={"005930":"종가 범위 오류"}
            for changed in (
                status,
                {**status,"failed_bar_diagnostics":[["000660",
                  status["failed_bar_diagnostics"][0][1]]]},
                {**status,"failed_bar_diagnostics":[["005930",{
                    **status["failed_bar_diagnostics"][0][1],
                    "official_adjustment_certified":True}]]}
            ):
                file.write_text(json.dumps(changed),encoding="utf-8")
                self.assertEqual(load_collector_tail_diagnostics(
                    folder,"2026-11-02",["005930"],errors),{})

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
