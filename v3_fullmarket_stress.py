"""Synthetic 2,000+ issuer v3 scale and global-cohort preservation guard.

NO live market/industry assertions. Benchmark figures are environment-dependent,
not SLAs. Compute global sector peer percentiles BEFORE publication slices;
never normalize each 100-stock slice independently.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import time
import tracemalloc

from v3_offline_workbench import run_workbench
from v3_research_factor_engine import FIELD_SPECS, research_factor_batch

MAX_SYNTHETIC=20000

class ScaleError(ValueError):
    """Fixed safe error reason codes."""


def synthetic_issuer_inputs(n):
    if type(n) is not int or not 2000<=n<=MAX_SYNTHETIC:
        raise ScaleError("synthetic_test_needs_2000_to_20000_issuers")
    sparse=[]
    full=[]
    for i in range(n):
        market="KOSPI" if i<n//2 else "KOSDAQ"
        j=i if market=="KOSPI" else i-n//2
        code=f"{j+1+(100000 if market=='KOSDAQ' else 0):06d}"
        issuer=f"{j+1+(10000000 if market=='KOSDAQ' else 0):08d}"
        identity={"market":market,"code":code,"issuer_id":issuer,
                  "asof":"2026-10-16"}
        # Sparse workbench deliberately has no claimed official stock data.
        sparse.append(dict(identity))
        features={}
        for field, (_,direction,bounds) in FIELD_SPECS.items():
            if bounds==(0,1):
                value=0.2+(i%100)/500
            elif field=="valuation_pbr":
                value=0.6+(i%90)/100
            elif field=="technical_volume_breakout_ratio":
                value=1+(i%30)/100
            else:
                value=float(1+(i%230)/10)
            features[field]=value
        full.append({**identity,"features":features})
    return sparse,full


def partition_global_output(rows, *, part_size=100):
    if (not isinstance(rows,list) or type(part_size) is not int
            or not 1<=part_size<=1000):
        raise ScaleError("invalid_fullmarket_partition")
    ordered=sorted(rows,key=lambda x:(x["market"],x["code"]))
    symbols={(r["market"],r["code"],r["issuer_id"]) for r in ordered}
    if len(symbols)!=len(ordered):
        raise ScaleError("duplicate_issuer_in_global_research")
    chunks=[ordered[i:i+part_size] for i in range(0,len(ordered),part_size)]
    if sum(len(x) for x in chunks)!=len(ordered):
        raise ScaleError("truncated_fullmarket_partition")
    return chunks


def stress_fullmarket(*, synthetic_count=2600, slice_size=100):
    """Exercise global scoring + research risk/quality + loss-safe partitions."""
    sparse, full=synthetic_issuer_inputs(synthetic_count)
    tracemalloc.start()
    start=time.perf_counter()
    global_scored=research_factor_batch(full,asof="2026-10-16")
    if len(global_scored["rows"])!=synthetic_count:
        raise ScaleError("global_scoring_universe_missing")
    if any(row["confirmed_buy"] or row["confirmed_score"] is not None
           for row in global_scored["rows"]):
        raise ScaleError("unverified_global_market_score_released")
    chunks=partition_global_output(global_scored["rows"],part_size=slice_size)
    if sum(len(c) for c in chunks)!=synthetic_count:
        raise ScaleError("partition_result_incomplete")
    # Run a *separate* low-data workbench to exercise mandatory no-source
    # quality gates on the same size universe without fabricating OHLC history.
    sparse_report=run_workbench({
        "asof":"2026-10-16","stocks":sparse
    })
    if (sparse_report["research_stock_count"]!=synthetic_count
            or sparse_report["fully_covered_six_pillar_research_only"]!=0
            or sparse_report["input_issue_counts"].get("quality_blocked")!=synthetic_count
            or sparse_report["v3_release_ready"] is not False):
        raise ScaleError("missing_data_accidentally_released_as_confirmed")
    duration=round((time.perf_counter()-start)*1000,2)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    expected_set={(r["market"],r["code"],r["issuer_id"])
                  for r in global_scored["rows"]}
    partition_set={(r["market"],r["code"],r["issuer_id"])
                   for chunk in chunks for r in chunk}
    if partition_set!=expected_set:
        raise ScaleError("partition_silent_survivor_drop")
    # Stable integrity fingerprint of all global scoring identifiers and
    # research-only points; excludes raw price, financial and flow payloads.
    canonical=json.dumps(
        [(r["market"],r["code"],r["candidate_full_total_research_only"])
         for chunk in chunks for r in chunk],
        separators=(",",":"),ensure_ascii=False,allow_nan=False)
    return {
        "schema":"v3-fullmarket-synthetic-stress-0.1",
        "dataset":"SYNTHETIC_ONLY","input_issuers":synthetic_count,
        "global_scored_research_candidates":len(global_scored["rows"]),
        "max_two_market_peer_cohort_size":max(global_scored["market_cohorts"].values()),
        "partitioned_chunks":len(chunks),
        "max_chunk_size":slice_size,
        "global_normalization_before_partition":True,
        "global_output_canonical_sha256":sha256(canonical.encode()).hexdigest(),
        "all_missing_source_quality_blocked":(
            sparse_report["input_issue_counts"]["quality_blocked"]==synthetic_count),
        "elapsed_runtime_ms_environment_specific":duration,
        "tracemalloc_peak_mib_environment_specific":round(peak/1048576,2),
        "price_bar_250_day_integrated_fullmarket_tested":False,
        "real_provider_fullmarket_latency_tested":False,
        "real_delisted_universe_completeness_authenticated":False,
        "confirmed_stock_rankings":None,
        "certified_prediction":None,
        "confirmed_buy":False,"v3_release_ready":False,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description="Synthetic full-market v3 local stress")
    parser.add_argument("--synthetic",type=int,default=2600)
    parser.add_argument("--slice-size",type=int,default=100)
    args=parser.parse_args(argv)
    try:
        result=stress_fullmarket(synthetic_count=args.synthetic,
                                 slice_size=args.slice_size)
    except ScaleError:
        print('{"status":"synthetic_stress_blocked","confirmed_buy":false}')
        return 1
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
