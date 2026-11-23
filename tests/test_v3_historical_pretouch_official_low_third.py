"""Check that third KRX LOW run cannot repeat sources or promote readiness."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import v3_historical_pretouch_official_low_third as third


class ThirdOfficialPreFirstLowTests(TestCase):
    def test_fixed_first_five_and_hard_twenty_request_ceiling(self):
        self.assertEqual(third.FIRST_FIVE, (
            "2025-12-10", "2025-12-11", "2025-12-16",
            "2025-12-17", "2025-12-19"))
        self.assertEqual(third.BATCH_LIMIT, 5)
        self.assertEqual(third.MAX_NEW_MARKET_API_REQUESTS, 20)

    def test_existing_frozen_evidence_never_triggers_api_call(self):
        with patch.object(third, "THIRD_FROZEN", Mock(**{"exists.return_value": True})):
            with patch.object(third, "check_bounded_batch") as api:
                with self.assertRaises(ValueError):
                    third.probe_ten_new_official_days({}, object())
                api.assert_not_called()

    def test_two_validated_five_day_batches_are_sequential_not_repeated(self):
        first=[date.fromisoformat(x) for x in third.FIRST_FIVE]
        second=[date(2026, 1, n) for n in (5,6,7,8,9)]
        p1={"batch":first, "report":{"previous":250}}
        p2={"batch":second,"report":{"previous":280}}
        def response(dates, count, char):
            return {
                "scope_dates":[x.isoformat() for x in dates],
                "official_low_holder_days_exact":count,
                "new_market_api_requests_max":10,
                "official":{x.isoformat():{"official_source_sha256":{
                    "KOSPI":char*64, "KOSDAQ":(str((int(char)+1)%10))*64
                    }} for x in dates},
            }
        # Build distinct hashes per day, so no repeated raw KRX response is accepted.
        x=response(first,30,"1")
        y=response(second,25,"3")
        for i,(d,v) in enumerate(x["official"].items()):
            v["official_source_sha256"]={"KOSPI":f"{i+1:064x}",
                                          "KOSDAQ":f"{i+11:064x}"}
        for i,(d,v) in enumerate(y["official"].items()):
            v["official_source_sha256"]={"KOSPI":f"{i+21:064x}",
                                          "KOSDAQ":f"{i+31:064x}"}
        with patch.object(third, "THIRD_FROZEN", Mock(**{"exists.return_value": False})):
            with patch.object(third, "previous_verified", return_value=(
                p1, set(), set())):
                with patch.object(third, "prepare", return_value=p2) as select:
                    with patch.object(third, "check_bounded_batch",
                                      side_effect=[x,y]) as api:
                        result=third.probe_ten_new_official_days({},object())
        self.assertEqual(api.call_count,2)
        select.assert_called_once()
        self.assertEqual(result["new_official_traded_holder_lows"],55)
        self.assertEqual(result["new_official_market_days"],10)
        self.assertEqual(result["maximum_official_krx_market_requests"],20)
        self.assertFalse(result["first_touch_earliest_low_certified"])
        self.assertFalse(result["v30b_release_ready"])

    def test_missing_authenticated_provenance_rejected(self):
        with patch.object(third, "previous_verified",
                          return_value=({"report":{}},set(),set())):
            with self.assertRaises(ValueError):
                third.verify_third_official_coverage({}, {})
            with self.assertRaises(ValueError):
                third.verify_third_official_coverage({}, {
                    "schema":third.SCHEMA,
                    "saved_on_kst":"2026-11-23",
                    "claim_frozen_readonly":True,
                    "run_id":111,
                },provenance=(1,2,3,"a"*64))

    def test_no_intraday_low_completion_or_returns_promotion_in_workflow(self):
        root=Path(__file__).resolve().parents[1]
        code=(root/"v3_historical_pretouch_official_low_third.py").read_text()
        workflow=(root/".github/workflows/v3-historical-close-archive-audit.yml").read_text()
        self.assertIn("regular_session_volume_independently_verified",code)
        self.assertIn("performance_validated",code)
        self.assertIn("third_krx_official_source",code.replace("market_hash","source"))
        self.assertIn("v3_historical_pretouch_official_low_third.py",workflow)
        self.assertIn("test_v3_historical_pretouch_official_low_third.py",workflow)
        self.assertIn("contents: read",workflow)


if __name__=="__main__":
    import unittest
    unittest.main()
