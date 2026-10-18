"""Offline mock two-market pagination, fault injection, no partial release."""
import unittest
import hashlib
from v3_atomic_ingestion import (
    IngestionError, RetryableCollectionError, collect_atomic_pair
)
DATE="2026-10-16"

def roster(n=5):
    return {market:[{"code":f"{(k+(100000 if market=='KOSDAQ' else 0)):06d}",
                      "issuer_id":f"{(k+(10000000 if market=='KOSDAQ' else 0)):08d}"}
                    for k in range(1,n+1)] for market in ("KOSPI","KOSDAQ")}

def fetcher(expected, state=None):
    state=state or {}
    def page(market,asof,cursor):
        if state.get("outage_market")==market:
            raise RetryableCollectionError()
        if state.get("fatal_market")==market:
            raise RuntimeError("SECRET_NOT_PRINTED")
        rows=expected[market][:3] if cursor is None else expected[market][3:]
        if market=="KOSDAQ":
            rows=expected[market] if cursor is None else []
        output=[{**r,"market":market,"asof":asof,"close":100.0,"volume":120}
                for r in rows]
        if state.get("partial_market")==market and cursor is not None:
            output=output[:-1]
        if state.get("wrong_day")==market:
            asof="2026-10-15"
        if state.get("duplicate")==market and cursor is not None:
            output=[{**expected[market][0],"market":market,"asof":asof,
                     "close":100.0,"volume":120}]
        next_cursor=("PAGE2" if cursor is None and market=="KOSPI" else None)
        if state.get("loop")==market:
            next_cursor="PAGE2"
        return {"asof":asof,"market":market,"rows":output,"next_cursor":next_cursor}
    return page

class AtomicIngestionTests(unittest.TestCase):
    def test_success_two_market_pages_sha_bytes_and_no_auth(self):
        expected=roster()
        r=collect_atomic_pair(fetcher(expected),asof=DATE,
                              expected_roster=expected)
        self.assertTrue(r["both_markets_captured_together"])
        self.assertEqual(r["market_audit"]["KOSPI"]["pages"],2)
        self.assertEqual(r["market_audit"]["KOSDAQ"]["pages"],1)
        for market in ("KOSPI","KOSDAQ"):
            self.assertEqual(r["market_audit"][market]["records"],5)
            self.assertEqual(r["market_audit"][market]["canonical_sha256"],
                             hashlib.sha256(r["private_unpublished_payload_bytes"][market]).hexdigest())
        self.assertFalse(r["safe_for_public_release"])
        self.assertFalse(r["official_provider_data_verified"])
        self.assertFalse(r["confirmed_buy"])

    def test_retry_transient_only_same_page_without_partial(self):
        expected=roster()
        calls={}
        base=fetcher(expected)
        def once(market,asof,cursor):
            k=(market,cursor)
            calls[k]=calls.get(k,0)+1
            if k==("KOSPI",None) and calls[k]==1:
                raise RetryableCollectionError()
            return base(market,asof,cursor)
        r=collect_atomic_pair(once,asof=DATE,expected_roster=expected)
        self.assertEqual(r["market_audit"]["KOSPI"]["retryable_failures"],1)
        self.assertEqual(calls[("KOSPI",None)],2)

    def test_no_silent_yesterday_fallback_on_missing_kosdaq(self):
        exp=roster()
        for option in ({"outage_market":"KOSDAQ"},{"partial_market":"KOSPI"},
                       {"wrong_day":"KOSDAQ"},{"duplicate":"KOSPI"},
                       {"fatal_market":"KOSDAQ"},{"loop":"KOSPI"}):
            with self.subTest(option=option), self.assertRaises(IngestionError):
                collect_atomic_pair(fetcher(exp,option),asof=DATE,
                                    expected_roster=exp,attempts_per_page=2)

    def test_incomplete_or_survivor_roster_does_not_autofill(self):
        original=roster()
        expected=roster()
        expected["KOSPI"].append({"code":"123456","issuer_id":"87654321"})
        with self.assertRaisesRegex(IngestionError,"incomplete"):
            collect_atomic_pair(fetcher(original),asof=DATE,
                                expected_roster=expected)

    def test_invalid_issuers_and_roster_dup_and_boolean_volume_block(self):
        exp=roster()
        exp["KOSPI"].append(dict(exp["KOSPI"][0]))
        with self.assertRaises(IngestionError):
            collect_atomic_pair(fetcher(exp),asof=DATE,expected_roster=exp)
        expected=roster()
        base=fetcher(expected)
        def bad(market,asof,cursor):
            p=base(market,asof,cursor)
            p["rows"][0]["volume"]=True
            return p
        with self.assertRaisesRegex(IngestionError,"invalid_price_volume"):
            collect_atomic_pair(bad,asof=DATE,expected_roster=expected)

    def test_explicit_bounded_pagination_retry_and_fail_closed(self):
        expected=roster()
        with self.assertRaisesRegex(IngestionError,"page_limit"):
            collect_atomic_pair(fetcher(expected),asof=DATE,
                                expected_roster=expected,max_pages_per_market=1)
        with self.assertRaisesRegex(IngestionError,"retry_budget"):
            collect_atomic_pair(fetcher(expected,{"outage_market":"KOSPI"}),
                                asof=DATE,expected_roster=expected,
                                attempts_per_page=1)

    def test_no_network_or_keys_in_collector_output(self):
        r=collect_atomic_pair(fetcher(roster()),asof=DATE,expected_roster=roster())
        self.assertNotIn("API_KEY",str(r))
        self.assertTrue(r["collection_adapter_injected_no_builtin_network"])

if __name__=="__main__":
    unittest.main()
