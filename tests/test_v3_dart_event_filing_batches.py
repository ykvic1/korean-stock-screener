"""Research-only historical DART receipt batch safety tests."""
import gzip
import json
import unittest
from v3_dart_event_filing_batches import (
    _ranking,_batch_file,_check_report,_check_cursor,
    DARTEventBatchError,SCHEMA,MAX_NEXT_ISSUERS)


class DARTBatchTests(unittest.TestCase):
    def test_rank_dedup_and_one_chunk_ten_issuers(self):
        rows=[{"issuer_market":"KOSPI:005930","quantized_change_bp":-4600},
              {"issuer_market":"KOSPI:005930","quantized_change_bp":3000},
              {"issuer_market":"KOSDAQ:035420","quantized_change_bp":3600}]
        self.assertEqual(_ranking(rows),["005930","035420"])
        self.assertEqual(MAX_NEXT_ISSUERS,10)
        self.assertEqual(_batch_file(6,16),
                         "derived/quality/dart-batches/2016-12-16_006_016.json.gz")

    def test_partial_filing_queries_cannot_certify_corporate_events_or_ias(self):
        obj={"schema":SCHEMA,"first_issuer_index":6,"next_issuer_index":7,
             "first_day":"2015-01-01","last_day":"2017-12-31",
             "risk_baseline_end":"2016-12-16",
             "source_krx_calls":0,"original_dart_filing_rows_saved":False,
             "corporate_actions_certified":False,"v3_scores_certified":False,
             "issuer_results":[{"issuer_code":"005930","status":"unknown"}]}
        raw=gzip.compress(json.dumps(obj).encode(),mtime=0)
        self.assertFalse(_check_report(raw,6,7)["v3_scores_certified"])
        obj["corporate_actions_certified"]=True
        with self.assertRaises(DARTEventBatchError):
            _check_report(gzip.compress(json.dumps(obj).encode(),mtime=0),6,7)

    def test_bad_private_incremental_cursor_denied(self):
        with self.assertRaises(DARTEventBatchError):
            _check_cursor(b'{"schema":"bad"}',["000001"]*20,"f"*64)


if __name__=="__main__":
    unittest.main()
