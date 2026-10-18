"""2k+ synthetic full-market scoring load without official data or rankings."""
from __future__ import annotations
import unittest
from v3_fullmarket_stress import (
    ScaleError,partition_global_output,stress_fullmarket,
    synthetic_issuer_inputs
)
from v3_offline_workbench import run_workbench,WorkbenchError

class FullMarketLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report=stress_fullmarket(synthetic_count=2200,slice_size=100)

    def test_2200_issuers_complete_global_peer_comparison_and_safe_gates(self):
        r=self.report
        self.assertEqual(r["input_issuers"],2200)
        self.assertEqual(r["global_scored_research_candidates"],2200)
        self.assertEqual(r["partitioned_chunks"],22)
        self.assertEqual(r["max_chunk_size"],100)
        self.assertTrue(r["global_normalization_before_partition"])
        self.assertTrue(r["all_missing_source_quality_blocked"])
        self.assertEqual(r["dataset"],"SYNTHETIC_ONLY")
        self.assertFalse(r["confirmed_buy"])
        self.assertFalse(r["v3_release_ready"])
        self.assertIsNone(r["confirmed_stock_rankings"])
        self.assertIsNone(r["certified_prediction"])
        self.assertGreaterEqual(r["max_two_market_peer_cohort_size"],1000)

    def test_stress_time_memory_are_measurements_not_hardcoded_or_proven_sla(self):
        r=self.report
        self.assertGreater(r["elapsed_runtime_ms_environment_specific"],0)
        self.assertGreater(r["tracemalloc_peak_mib_environment_specific"],0)
        self.assertFalse(r["real_provider_fullmarket_latency_tested"])
        self.assertFalse(r["price_bar_250_day_integrated_fullmarket_tested"])
        self.assertFalse(r["real_delisted_universe_completeness_authenticated"])

    def test_market_identity_and_repeated_slice_partition_is_lossless(self):
        sparse,full=synthetic_issuer_inputs(2000)
        self.assertEqual(len(sparse),2000)
        self.assertEqual(len(full),2000)
        self.assertEqual(set(x["market"] for x in sparse),{"KOSPI","KOSDAQ"})
        fake=[{"market":x["market"],"code":x["code"],
               "issuer_id":x["issuer_id"],"candidate_full_total_research_only":12}
              for x in sparse]
        pieces=partition_global_output(fake,part_size=100)
        self.assertEqual(len(pieces),20)
        self.assertEqual(sum(len(x) for x in pieces),2000)
        self.assertTrue(all(len(x)==100 for x in pieces))
        self.assertEqual({(v["market"],v["code"]) for c in pieces for v in c},
                         {(v["market"],v["code"]) for v in fake})

    def test_same_symbol_repeated_in_publication_chunk_blocked(self):
        sample=[{"market":"KOSPI","code":"000001","issuer_id":"00000001"}]
        with self.assertRaisesRegex(ScaleError,"duplicate"):
            partition_global_output(sample+sample)
        with self.assertRaises(ScaleError):
            synthetic_issuer_inputs(50)
        with self.assertRaises(ScaleError):
            partition_global_output(sample,part_size=0)

    def test_unverified_single_corrupt_date_never_allows_partial_report(self):
        s,_=synthetic_issuer_inputs(2000)
        s[50]["asof"]="2026-10-15"
        with self.assertRaises(WorkbenchError):
            run_workbench({"asof":"2026-10-16","stocks":s[:80]})

if __name__=="__main__":
    unittest.main()
