"""Check the next five previously unqueried KRX pre-first-touch LOW dates."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from official_probe.clients import KRXClient
from v3_historical_market_coverage import ROOT, read_panels, require
from v3_historical_pretouch_official_low_gap import check_bounded_batch
from v3_historical_pretouch_official_low_second import SECOND_FROZEN
from v3_historical_pretouch_official_low_third import (
    FIRST_FROZEN,
    THIRD_FROZEN,
    verify_third_official_coverage,
)

FOURTH_FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-fourth-20261123.json"
NEXT_DATES = ("2026-01-02", "2026-01-06", "2026-02-12", "2026-02-19", "2026-02-20")
EXPECTED_HOLDER_LOWS = 21


def probe_next_official_days(panels, client):
    require(not FOURTH_FROZEN.exists(), "fourth_official_low_evidence_already_frozen")
    third = json.loads(THIRD_FROZEN.read_text(encoding="utf-8"))
    plan = verify_third_official_coverage(panels, third)
    report = plan["report"]
    require(report["pre_touch_public_traded_holder_days"] == 752
            and report["pre_touch_holder_days_on_existing_official_ohlc_dates"] == 308
            and report["pre_touch_holder_days_without_existing_official_ohlc_date"] == 444
            and tuple(d.isoformat() for d in plan["batch"]) == NEXT_DATES
            and report["bounded_next_issuer_low_observations"] == EXPECTED_HOLDER_LOWS,
            "fourth_official_low_plan_changed")

    earlier_batches = [
        json.loads(path.read_text(encoding="utf-8"))["result"]
        for path in (FIRST_FROZEN, SECOND_FROZEN)
    ] + third["result"]["two_five_day_batches"]
    seen = {
        digest for batch in earlier_batches
        for evidence in batch["official"].values()
        for digest in evidence["official_source_sha256"].values()
    }
    require(len(seen) == 40, "fourth_prior_source_hashes_incomplete")
    result = check_bounded_batch(panels, plan, client)
    new_digests = {
        digest for evidence in result["official"].values()
        for digest in evidence["official_source_sha256"].values()
    }
    require(tuple(result["scope_dates"]) == NEXT_DATES
            and result["official_low_holder_days_exact"] == EXPECTED_HOLDER_LOWS
            and result["new_market_api_requests_max"] == 10
            and len(new_digests) == 10 and new_digests.isdisjoint(seen),
            "fourth_official_low_result_scope_or_source_changed")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    args = parser.parse_args(argv)
    try:
        key = os.environ.get("KRX_API_KEY", "").strip()
        require(key, "fourth_krx_secret_not_available")
        print(json.dumps(probe_next_official_days(read_panels(args.sources), KRXClient(key)),
                         sort_keys=True))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        reason = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{5,99}", reason):
            reason = "unclassified_input_error"
        parser.exit(1, "fourth_krx_official_low_evidence_failed:" + reason + "\n")


if __name__ == "__main__":
    main()
