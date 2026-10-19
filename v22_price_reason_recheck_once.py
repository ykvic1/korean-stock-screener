"""One-time bounded read-only recheck of CURRENTLY LISTED error-only stocks.

Inspect real 2026-10-19 price failures without publishing stock codes, raw
OHLCV or provider errors. This is a small sample, NOT 106 repairs/validation.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
from zoneinfo import ZoneInfo

from v22_price_error_triage import public_reason

CODES = re.compile(r"\d{6}\Z")
MAX_SAMPLE = 24


def sample_errors(catalog, asof):
    if (catalog.get("schema")!="v22-current-listed-catalog-0.1"
            or catalog.get("date")!=asof
            or catalog.get("listed_total")!=len(catalog.get("rows",[]))):
        raise ValueError("price_triage_catalog_outdated_or_unaccounted")
    rows = [r for r in catalog["rows"]
            if isinstance(r,dict)
            and r.get("price_history_status")=="price_source_or_validation_error"
            and isinstance(r.get("code"),str)
            and CODES.fullmatch(r["code"])]
    if len(rows)<12 or len(rows)>2000 or len(set(r["code"] for r in rows))!=len(rows):
        raise ValueError("price_triage_error_population_invalid")
    # Deterministic, dispersed sample in each market without a privileged
    # stock selection; no user-side symbol list.
    chosen=[]
    for market in ("KOSPI","KOSDAQ"):
        items=sorted(r["code"] for r in rows if r.get("market")==market)
        if not items:
            continue
        count=min(MAX_SAMPLE//2,len(items))
        idx=[min(len(items)-1,(i*len(items))//count) for i in range(count)]
        chosen.extend(items[i] for i in idx)
    return chosen[:MAX_SAMPLE],len(rows)


def main():
    import screener
    asof="2026-10-19"
    catalog=json.loads(Path("docs/v22-market-catalog.json").read_text(encoding="utf8"))
    samples,n=sample_errors(catalog,asof)
    cfg=screener.cfg_read()
    day=datetime.fromisoformat(asof)
    results=Counter()
    for i,code in enumerate(samples):
        try:
            # The same unchanged official v2.2 read contract, not alternate
            # fabricated prices. Test date is strictly the published day.
            history,last=screener.last_series(None,code,cfg,day)
            if last.isoformat()==asof:
                results["recovered_at_same_asof_on_retry"]+=1
            else:
                results["sample_returns_wrong_session"]+=1
        except screener.InsufficientHistory:
            results["price_insufficient_history_on_retry"]+=1
        except Exception as exc:
            results[public_reason(exc)]+=1
        if i+1<len(samples):
            time.sleep(0.15)
    if sum(results.values())!=len(samples):
        raise ValueError("sample_not_accounted")
    output={
        "status":"research_sample_only_not_live_recovery",
        "asof":asof,
        "total_failed_price_codes":n,
        "sampled":len(samples),
        "causes":dict(sorted(results.items())),
        "raw_price_copied":False,
        "all_129_validated":False,
        "v3_six_factor_score_allowed":False,
    }
    print(json.dumps(output,ensure_ascii=False,sort_keys=True))


if __name__=="__main__":
    main()
