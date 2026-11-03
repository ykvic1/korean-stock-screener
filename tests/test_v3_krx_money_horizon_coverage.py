"""Offline immutable-archive replay and aggregate-only money research guards."""
import copy
import gzip
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from test_v3_krx_money_horizon_research import input_cohorts
from v3_krx_money_backfill import CHECKPOINT, SCHEMA as CHECKPOINT_SCHEMA, _path
from v3_krx_money_horizon_coverage import collect, MoneyCoverageError


class Storage:
    repository="ykvic1/korean-stock-private-data"

    def __init__(self, files):
        self.files=files
        self.changed=False
        self.checkpoint_reads=0

    def verify_private(self):
        return True

    def get_content(self,path):
        if path==CHECKPOINT:
            self.checkpoint_reads+=1
            if self.changed and self.checkpoint_reads>=4:
                return b"changed","new-version"
        raw=self.files.get(path)
        return raw, sha256(raw).hexdigest() if raw else None


def fixture():
    from datetime import date,timedelta
    entries=input_cohorts(8)
    dates=[]
    files={}
    for i, entry in enumerate(entries):
        cohort=entry["cohort"]
        factors=cohort["derived_issuer_money_factors"]
        # Three synthetic issuer-market pairs: increased, missing baseline,
        # and a genuinely observed zero-amount previous period.
        for code in ("000002","000003"):
            factor=copy.deepcopy(factors["KOSPI:000001"])
            factor["issuer_code"]=code
            factors["KOSPI:"+code]=factor
        if i>=4:
            factors["KOSPI:000003"]["total_actual_amount_100k_krw"]=0
        else:
            factors["KOSPI:000001"]["total_actual_amount_100k_krw"]=10000
        if i==5:
            del factors["KOSPI:000002"]
        start=date.fromisoformat(cohort["window_start"])
        window=[(start+timedelta(days=k)).isoformat() for k in range(5)]
        dates=window+dates
        cohort.update({"source_api_calls":10,"raw_vendor_source_retained":False,
                       "complete_five_session_issuer_market_pairs":len(factors),
                       "market_date_coverage":{str(k):{} for k in range(10)}})
        files[_path(window)]=gzip.compress(json.dumps(cohort).encode())
    last=entries[-1]["cohort"]
    tail_path=f'derived/money-five-session/{last["window_start"]}_{last["window_end"]}.json.gz'
    state={"schema":CHECKPOINT_SCHEMA,"start":"2016-10-17","end":"2026-10-16",
           "calendar_candidate_hash":"a"*64,"next_window_index":8,
           "full_issuer_bucket_records":23,
           "compressed_factor_bytes":sum(map(len,files.values())),
           "last_window_file_sha256":sha256(files[tail_path]).hexdigest(),
           "raw_source_file_count":0}
    files[CHECKPOINT]=json.dumps(state).encode()
    return Storage(files),dates,"a"*64


class MoneyHorizonCoverageTests(unittest.TestCase):
    def test_actual_private_blobs_replay_into_counts_without_issuer_amounts(self):
        storage,dates,digest=fixture()
        result=collect(storage,dates,digest)
        row=result["daily_disjoint_period_coverage"]["20"]
        self.assertEqual(row["latest_window_complete_issuer_market_pairs"],3)
        self.assertEqual(row["disjoint_comparison_complete_issuer_market_pairs"],2)
        self.assertEqual(row["zero_baseline_issuer_market_pairs"],1)
        self.assertEqual(row["positive_baseline_comparable_issuer_market_pairs"],1)
        self.assertEqual(row["higher_average_amount_issuer_market_pairs"],1)
        self.assertEqual(row["every_recent_cohort_above_previous_average_issuer_market_pairs"],1)
        self.assertEqual(result["daily_disjoint_period_coverage"]["60"]
                         ["latest_window_complete_issuer_market_pairs"],0)
        self.assertEqual(result["historical_archive_endpoint"],dates[-1])
        self.assertFalse(result["current_market_session_alignment_verified"])
        self.assertFalse(result["scoring_allowed"])
        self.assertIsNone(result["investment_points"])
        text=json.dumps(result)
        for forbidden in ("000001","000002","average_reported_amount","money_pressure_intrabucket_bp"):
            self.assertNotIn(forbidden,text)

    def test_missing_interior_file_cannot_be_called_consecutive_history(self):
        storage,dates,digest=fixture()
        del storage.files[_path(dates[10:15])]
        with self.assertRaisesRegex(MoneyCoverageError,"missing_or_invalid"):
            collect(storage,dates,digest)

    def test_wrong_window_metadata_and_bad_persistence_are_blocked(self):
        for field,value in (("window_end","2026-10-01"),
                            ("late2_vs_early2_money_pressure_bp",99999)):
            storage,dates,digest=fixture()
            name=_path(dates[-5:])
            obj=json.loads(gzip.decompress(storage.files[name]))
            if field=="window_end":
                obj[field]=value
            else:
                obj["derived_issuer_money_factors"]["KOSPI:000001"][field]=value
            storage.files[name]=gzip.compress(json.dumps(obj).encode())
            with self.subTest(field=field), self.assertRaises(MoneyCoverageError):
                collect(storage,dates,digest)

    def test_concurrent_checkpoint_advance_cannot_publish_mixed_counts(self):
        storage,dates,digest=fixture()
        storage.changed=True
        with self.assertRaisesRegex(MoneyCoverageError,"changed_during_read"):
            collect(storage,dates,digest)

    def test_read_limit_cannot_expand_to_entire_archive(self):
        from v3_krx_money_horizon_coverage import MAX_COHORTS
        self.assertEqual(MAX_COHORTS,96)
        storage,dates,digest=fixture()
        storage.repository="public/wrong-destination"
        with self.assertRaisesRegex(MoneyCoverageError,"unexpected"):
            collect(storage,dates,digest)
        self.assertEqual(storage.checkpoint_reads,0)


if __name__=="__main__":
    unittest.main()
