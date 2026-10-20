"""Five-session real KRX size calibration. Never writes or uploads source rows.

Authorized Open API EOD endpoints: two official market calls per completed day.
Outputs counts/byte totals only, with a fixed ten-call cap and no stock identifiers.
No private-archive or repository-secrets writes. Historical retrieval is a probe,
NOT proof of licensed persistent storage or confirmed V3 factor coverage.
"""
from __future__ import annotations

from datetime import date
import json
from typing import Any

from v3_krx_private_github_backfill import _pack_pair, _unpack_pair

DAYS = ("2026-10-12","2026-10-13","2026-10-14","2026-10-15","2026-10-16")
MARKETS = ("KOSPI","KOSDAQ")


class SizeProbeError(ValueError):
    """Fixed safe error labels only; never include the vendor payload."""


def measure(client: Any, dates=DAYS) -> dict:
    if (type(dates) not in (tuple,list) or len(dates)!=5
        or len(set(dates))!=5 or list(dates)!=sorted(dates)
        or any(type(d) is not str or date.fromisoformat(d).weekday()>4 for d in dates)):
        raise SizeProbeError("invalid_fixed_five_session_probe")
    raw_sum=0
    compressed_sum=0
    total_rows=0
    daily=[]
    n_calls=0
    for day in dates:
        market_bytes={}
        for market in MARKETS:
            try:
                rows,raw=client.daily_market(market,day)
                if (type(raw) is not bytes or not isinstance(rows,list)
                    or json.loads(raw).get("OutBlock_1")!=rows):
                    raise ValueError()
            except Exception:
                raise SizeProbeError("official_market_probe_failed") from None
            market_bytes[market]=raw
            n_calls+=1
        try:
            packed,by_market=_pack_pair(day,market_bytes)
            decoded=_unpack_pair(day,packed)
            if by_market!=decoded:
                raise ValueError()
        except Exception:
            raise SizeProbeError("five_day_roundtrip_validation_failed") from None
        raw_bytes=sum(x["raw_bytes"] for x in by_market.values())
        rows=sum(x["source_rows"] for x in by_market.values())
        raw_sum+=raw_bytes
        compressed_sum+=len(packed)
        total_rows+=rows
        daily.append({
            "date":day,
            "total_market_rows":rows,
            "raw_bytes":raw_bytes,
            "gzip_bytes":len(packed),
            "both_markets_validated":True,
        })
    if n_calls!=10:
        raise SizeProbeError("incorrect_official_api_call_count")
    return {
        "schema":"v3-real-five-day-krx-compression-probe-0.1",
        "data_origin":"official_KRX_API_in_memory_only",
        "sample_trade_days":len(dates),
        "total_official_market_calls":n_calls,
        "source_market_rows":total_rows,
        "raw_source_bytes":raw_sum,
        "gzip_source_bytes":compressed_sum,
        "gzip_saving_pct":round(100*(1-compressed_sum/raw_sum),2),
        "average_daily_gzip_bytes":round(compressed_sum/5),
        "ten_year_2450day_projection_mib":round(compressed_sum/5*2450/1048576,2),
        "private_archive_files_written":0,
        "raw_response_persisted":False,
        "licensed_permanent_retention_verified":False,
        "stock_level_ias_20_points_verified":False,
        "per_date":daily,
    }


def main() -> int:
    import os
    if not os.getenv("KRX_API_KEY","").strip():
        print('{"status":"blocked","reason":"missing_existing_krx_secret","calls":0}')
        return 1
    try:
        from official_probe.clients import KRXClient
        summary=measure(KRXClient(os.environ["KRX_API_KEY"]))
    except Exception:
        print('{"status":"blocked","reason":"provider_response_or_roundtrip_validation_failed","raw_data_published":false}')
        return 1
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
